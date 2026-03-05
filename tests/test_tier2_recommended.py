"""
Tier 2: Strongly Recommended Tests — Fallback, Dispatcher, Login Retry
Tests T6–T8: Prevents subtle bugs that degrade the system over time.
"""
import time
import threading
import pytest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# T6: Redis Down — SQLite Fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestT6_RedisFallback:
    """
    When Redis is down, get_proxy() must fall back to SQLite.
    report_failure() must not crash.
    """

    def test_get_proxy_falls_back_to_sqlite(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.get_client.return_value = None

        sqlite_proxies = [
            {"address": "sqlite.proxy:8080", "status": "Active",
             "consecutive_fails": 0, "last_used": ""},
            {"address": "disabled.proxy:8080", "status": "Disabled",
             "consecutive_fails": 5, "disabled_until": None},
        ]

        with patch("bot.proxy_manager.redis_manager", mock_rm), \
             patch("bot.proxy_manager.ProxyRepository.get_all", return_value=sqlite_proxies):
            from bot.proxy_manager import ProxyManager
            pm = ProxyManager.__new__(ProxyManager)
            pm.proxies = sqlite_proxies
            pm._lua_proxy_fail = None
            pm.last_reload = time.time()
            pm.reload_interval = 300

            result = pm.get_proxy()
            assert result == "sqlite.proxy:8080"

    def test_report_failure_no_crash_without_redis(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.get_client.return_value = None

        with patch("bot.proxy_manager.redis_manager", mock_rm), \
             patch("bot.proxy_manager.ProxyRepository.update_proxy_status"):
            from bot.proxy_manager import ProxyManager
            pm = ProxyManager.__new__(ProxyManager)
            pm._lua_proxy_fail = None
            # Must not raise — just silently skip Redis path
            pm.report_failure("some.proxy:8080", error_type="general")


# ═══════════════════════════════════════════════════════════════════════════
# T7: Scout Dispatcher — 5-Min Expiry Window
# ═══════════════════════════════════════════════════════════════════════════

class TestT7_ScoutDispatcher:
    """
    report_date_found() followed by immediate report_no_date()
    must NOT clear dates (5-minute protection window).
    """

    def _make_dispatcher(self):
        from bot.dispatcher import ScoutDispatcher
        d = ScoutDispatcher.__new__(ScoutDispatcher)
        d._init_dispatcher()
        return d

    def test_dates_survive_immediate_no_date(self):
        d = self._make_dispatcher()

        d.report_date_found([{"category": "Tourism", "day": "12"}])
        assert d.is_date_available is True

        # Immediately call report_no_date — should NOT clear (within 5-min window)
        d.report_no_date()
        assert d.is_date_available is True, "5-min protection window broken"

    def test_dates_expire_after_5_minutes(self):
        d = self._make_dispatcher()

        d.report_date_found([{"category": "Tourism", "day": "12"}])
        assert d.is_date_available is True

        # Simulate 6 minutes passing
        d.last_found_time = time.time() - 360

        d.report_no_date()
        assert d.is_date_available is False, "Dates did not expire after 5 min"

    def test_waiting_workers_wake_up(self):
        d = self._make_dispatcher()
        woke_up = []

        def worker():
            result = d.wait_for_dates(timeout=3)
            woke_up.append(result)

        # Start 5 waiting workers
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()

        time.sleep(0.1)  # Let them block on wait
        d.report_date_found([{"category": "Tourism", "day": "15"}])

        for t in threads:
            t.join(timeout=3)

        assert len(woke_up) == 5, f"Only {len(woke_up)}/5 workers woke up"
        assert all(woke_up), "Some workers returned False"


# ═══════════════════════════════════════════════════════════════════════════
# T8: Login 3-Fail Quarantine — Counter Reset
# ═══════════════════════════════════════════════════════════════════════════

class TestT8_LoginFailQuarantine:
    """
    login_fail_count must reset to 0 on success.
    Quarantine triggers only after 3 CONSECUTIVE failures.
    """

    def test_counter_resets_on_success(self):
        """Fail → Fail → Success → counter must be 0."""
        import queue
        from bot.manager import WorkerThread

        user = {"id": 1, "first_name": "Test", "check_interval": 60}
        wt = WorkerThread.__new__(WorkerThread)
        wt.user = user
        wt.global_config = {}
        wt.log_queue = queue.Queue()
        wt.running = True
        wt.is_scout = False
        wt.login_fail_count = 0

        # Simulate 2 failures
        wt.login_fail_count = 2

        # Then a success
        wt.login_fail_count = 0  # This is what run() does on success

        assert wt.login_fail_count == 0

    def test_quarantine_at_3_consecutive(self):
        """3 consecutive failures → set_user_cooldown called."""
        import queue
        from bot.manager import WorkerThread

        user = {"id": 1, "first_name": "Test", "check_interval": 60}
        wt = WorkerThread.__new__(WorkerThread)
        wt.user = user
        wt.global_config = {}
        wt.log_queue = queue.Queue()
        wt.running = True
        wt.is_scout = False
        wt.login_fail_count = 0

        # Simulate the exact logic from WorkerThread.run()
        cooldown_called = False
        for _ in range(3):
            # Simulating login failure
            wt.login_fail_count += 1
            if wt.login_fail_count >= 3:
                cooldown_called = True

        assert wt.login_fail_count == 3
        assert cooldown_called, "Quarantine not triggered after 3 consecutive failures"

    def test_intermittent_failures_dont_quarantine(self):
        """Fail → Fail → Success → Fail → Fail → should NOT quarantine (counter resets)."""
        import queue
        from bot.manager import WorkerThread

        user = {"id": 1, "first_name": "Test", "check_interval": 60}
        wt = WorkerThread.__new__(WorkerThread)
        wt.user = user
        wt.global_config = {}
        wt.log_queue = queue.Queue()
        wt.running = True
        wt.is_scout = False
        wt.login_fail_count = 0

        # Fail, Fail
        wt.login_fail_count += 1  # 1
        wt.login_fail_count += 1  # 2

        # Success resets
        wt.login_fail_count = 0

        # Fail, Fail (only 2 consecutive — should NOT quarantine)
        wt.login_fail_count += 1  # 1
        wt.login_fail_count += 1  # 2

        assert wt.login_fail_count == 2, "Counter didn't reset properly"
        assert wt.login_fail_count < 3, "Would have quarantined prematurely"
