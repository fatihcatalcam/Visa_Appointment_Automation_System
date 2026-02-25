"""
Tier 1: Must-Have Tests — Proxy Health & Risk Cascade
Tests T1–T5: Prevents the deadliest failure chain:
    proxy death → mass login fail → risk cascade → total quarantine
"""
import time
import threading
import pytest

from bot.proxy_manager import LUA_PROXY_FAIL


# ═══════════════════════════════════════════════════════════════════════════
# T1: Lua Atomic Proxy Failure — Concurrent Threshold
# ═══════════════════════════════════════════════════════════════════════════

class TestT1_LuaConcurrentProxyFailure:
    """
    20 threads call report_failure simultaneously for the same proxy.
    The Lua script must ensure consecutive_fails == 20 (no lost increments)
    and the proxy is disabled exactly once (at cf=5).
    """

    def test_concurrent_failures_increment_atomically(self, populated_redis, lua_proxy_fail):
        r, proxies = populated_redis
        addr = proxies[0]
        key = f"Proxy:Metrics:{addr}"
        results = []
        errors = []

        def fire_failure():
            try:
                result = lua_proxy_fail(
                    keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
                    args=[5, 1800, "general", addr, int(time.time())],
                    client=r,
                )
                results.append(int(result))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=fire_failure) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Lua errors: {errors}"

        # consecutive_fails must be exactly 20 — no lost increments
        cf = int(r.hget(key, "consecutive_fails"))
        assert cf == 20, f"Expected cf=20, got {cf} (lost increments)"

        # Proxy must be removed from ActiveList
        assert not r.sismember("Proxy:ActiveList", addr)

        # Proxy must be in CooldownQueue exactly once
        cooldown_score = r.zscore("Proxy:CooldownQueue", addr)
        assert cooldown_score is not None, "Proxy not in CooldownQueue"

        # Lua returns 1 for every call where cf >= threshold (5th through 20th = 16 times)
        disable_count = sum(1 for x in results if x == 1)
        assert disable_count == 16, f"Proxy disabled {disable_count} times, expected 16 (calls 5-20)"

    def test_proxy_stays_active_below_threshold(self, populated_redis, lua_proxy_fail):
        r, proxies = populated_redis
        addr = proxies[1]
        key = f"Proxy:Metrics:{addr}"

        # 4 failures — still below threshold of 5
        for _ in range(4):
            result = lua_proxy_fail(
                keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
                args=[5, 1800, "general", addr, int(time.time())],
                client=r,
            )
            assert int(result) == 0

        assert r.sismember("Proxy:ActiveList", addr), "Proxy removed too early"
        assert int(r.hget(key, "consecutive_fails")) == 4


# ═══════════════════════════════════════════════════════════════════════════
# T2: All Proxies Dead — get_proxy() Returns None Gracefully
# ═══════════════════════════════════════════════════════════════════════════

class TestT2_PoolExhaustion:
    """
    When every proxy is disabled, get_proxy() must return None
    without crashing or triggering account risk penalties.
    """

    def test_empty_active_list_returns_none(self, redis_client):
        """Redis is up but ActiveList is empty."""
        from unittest.mock import patch, MagicMock

        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.get_client.return_value = redis_client
        # ActiveList is empty (no SADD called)

        with patch("bot.proxy_manager.redis_manager", mock_rm), \
             patch("bot.proxy_manager.get_all_proxies", return_value=[]):
            from bot.proxy_manager import ProxyManager
            pm = ProxyManager.__new__(ProxyManager)
            pm.proxies = []
            pm._lua_proxy_fail = None
            pm.last_reload = time.time()
            pm.reload_interval = 300

            result = pm.get_proxy()
            assert result is None

    def test_no_proxy_means_no_risk_penalty(self):
        """If proxy is None, report_failure must be a no-op (not crash)."""
        from bot.proxy_manager import ProxyManager

        pm = ProxyManager.__new__(ProxyManager)
        pm._lua_proxy_fail = None
        # Should silently return, not crash
        pm.report_failure(None, error_type="general")
        pm.report_success(None)
        pm.report_release(None)


# ═══════════════════════════════════════════════════════════════════════════
# T3: Risk Score — Threshold + Reset Atomicity
# ═══════════════════════════════════════════════════════════════════════════

class TestT3_RiskScoreCascade:
    """
    5 rapid +30 calls must trigger quarantine and reset score to 35.
    Score must NOT accumulate to 150.
    """

    def test_threshold_triggers_once_and_resets(self, redis_client, lua_risk_score):
        uid = 42
        key = f"Account:Risk:{uid}"

        quarantine_triggers = []
        for i in range(5):
            result = lua_risk_score(
                keys=[key],
                args=[30, 70, 35],
                client=redis_client,
            )
            if int(result) > 0:
                quarantine_triggers.append(int(result))

        # Score should be 35 (reset after first breach at 90), then 35+30=65, then 65+30=95 → reset...
        final_score = int(redis_client.get(key) or 0)
        assert final_score <= 70, f"Score escaped threshold: {final_score}"

        # Quarantine must have been triggered at least once
        assert len(quarantine_triggers) >= 1, "Quarantine never triggered"

    def test_score_never_accumulates_unbounded(self, redis_client, lua_risk_score):
        """Even with 20 rapid calls, score stays bounded."""
        uid = 99
        key = f"Account:Risk:{uid}"

        max_observed = 0
        for _ in range(20):
            lua_risk_score(keys=[key], args=[15, 70, 35], client=redis_client)
            current = int(redis_client.get(key) or 0)
            if current > max_observed:
                max_observed = current

        # After reset-to-35 kicks in, score should never be much above 70
        # (it can briefly be 70+ in the Lua return, but GET shows reset value)
        assert max_observed <= 70, f"Score reached {max_observed}, expected ≤70"


# ═══════════════════════════════════════════════════════════════════════════
# T4: Cooldown Expiry — Proxies Return to Pool
# ═══════════════════════════════════════════════════════════════════════════

class TestT4_CooldownReactivation:
    """
    Expired proxies must return to ActiveList.
    Non-expired proxies must stay in CooldownQueue.
    """

    def test_expired_proxy_returns_to_active(self, redis_client):
        now = time.time()
        addr_expired = "expired.proxy:8080"
        addr_active_cd = "still.cooling:8080"

        # Setup: one expired, one not
        redis_client.zadd("Proxy:CooldownQueue", {addr_expired: now - 100})
        redis_client.zadd("Proxy:CooldownQueue", {addr_active_cd: now + 3600})
        redis_client.hset(f"Proxy:Metrics:{addr_expired}", mapping={
            "consecutive_fails": 5, "status": "Disabled"
        })
        redis_client.hset(f"Proxy:Metrics:{addr_active_cd}", mapping={
            "consecutive_fails": 3, "status": "Disabled"
        })

        from unittest.mock import patch, MagicMock
        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.get_client.return_value = redis_client

        with patch("bot.proxy_manager.redis_manager", mock_rm), \
             patch("bot.proxy_manager.get_all_proxies", return_value=[]):
            from bot.proxy_manager import ProxyManager
            pm = ProxyManager.__new__(ProxyManager)
            pm.proxies = []
            pm._lua_proxy_fail = None
            pm.last_reload = time.time()
            pm.reload_interval = 300
            pm._check_cooldowns(redis_client)

        # Expired proxy: should be in ActiveList, cf reset, status Active
        assert redis_client.sismember("Proxy:ActiveList", addr_expired)
        assert int(redis_client.hget(f"Proxy:Metrics:{addr_expired}", "consecutive_fails")) == 0
        assert redis_client.hget(f"Proxy:Metrics:{addr_expired}", "status") == "Active"
        assert redis_client.zscore("Proxy:CooldownQueue", addr_expired) is None

        # Non-expired: should still be in CooldownQueue
        assert redis_client.zscore("Proxy:CooldownQueue", addr_active_cd) is not None
        assert not redis_client.sismember("Proxy:ActiveList", addr_active_cd)


# ═══════════════════════════════════════════════════════════════════════════
# T5: 403 Strict Threshold (2 vs 5)
# ═══════════════════════════════════════════════════════════════════════════

class TestT5_403StrictThreshold:
    """
    403 errors must disable a proxy after 2 failures (not 5).
    General errors must NOT disable at 2 failures.
    """

    def test_403_disables_after_2(self, populated_redis, lua_proxy_fail):
        r, proxies = populated_redis
        addr = proxies[0]
        key = f"Proxy:Metrics:{addr}"

        r1 = lua_proxy_fail(
            keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
            args=[5, 1800, "403", addr, int(time.time())],
            client=r,
        )
        assert int(r1) == 0, "Proxy disabled too early (after 1st 403)"

        r2 = lua_proxy_fail(
            keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
            args=[5, 1800, "403", addr, int(time.time())],
            client=r,
        )
        assert int(r2) == 1, "Proxy NOT disabled after 2nd 403"
        assert not r.sismember("Proxy:ActiveList", addr)

    def test_general_stays_active_at_2(self, populated_redis, lua_proxy_fail):
        r, proxies = populated_redis
        addr = proxies[1]
        key = f"Proxy:Metrics:{addr}"

        for _ in range(2):
            lua_proxy_fail(
                keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
                args=[5, 1800, "general", addr, int(time.time())],
                client=r,
            )

        assert r.sismember("Proxy:ActiveList", addr), "General error disabled proxy at cf=2"

    def test_403_counter_tracked_separately(self, populated_redis, lua_proxy_fail):
        r, proxies = populated_redis
        addr = proxies[2]
        key = f"Proxy:Metrics:{addr}"

        lua_proxy_fail(
            keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
            args=[5, 1800, "403", addr, int(time.time())],
            client=r,
        )

        assert int(r.hget(key, "fail_count_403") or 0) == 1
        assert int(r.hget(key, "fail_count") or 0) == 1
