"""
Tier 3: Optional Tests — Connection Counter Edge Cases
Tests T9–T10: Prevents stale state from corrupting proxy routing.
"""
import time
import pytest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# T9: active_connections Counter Never Goes Negative
# ═══════════════════════════════════════════════════════════════════════════

class TestT9_ConnectionCounterFloor:
    """
    Double-release (report_release called twice) must not produce
    active_connections = -1, which would break least-connections routing.
    """

    def test_release_at_zero_stays_zero(self, redis_client):
        addr = "test.proxy:8080"
        redis_client.hset(f"Proxy:Metrics:{addr}", "active_connections", 0)

        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.get_client.return_value = redis_client

        with patch("bot.proxy_manager.redis_manager", mock_rm):
            from bot.proxy_manager import ProxyManager
            pm = ProxyManager.__new__(ProxyManager)
            pm._lua_proxy_fail = None
            pm.report_release(addr)

        val = int(redis_client.hget(f"Proxy:Metrics:{addr}", "active_connections"))
        assert val == 0, f"active_connections went to {val}, expected 0"

    def test_normal_increment_decrement(self, redis_client):
        addr = "test.proxy:8080"
        redis_client.sadd("Proxy:ActiveList", addr)
        redis_client.hset(f"Proxy:Metrics:{addr}", mapping={
            "active_connections": 0, "status": "Active"
        })

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

            # get_proxy increments
            proxy = pm.get_proxy()
            assert proxy == addr
            conn_after_get = int(redis_client.hget(f"Proxy:Metrics:{addr}", "active_connections"))
            assert conn_after_get == 1

            # release decrements
            pm.report_release(addr)
            conn_after_release = int(redis_client.hget(f"Proxy:Metrics:{addr}", "active_connections"))
            assert conn_after_release == 0


# ═══════════════════════════════════════════════════════════════════════════
# T10: Stale active_connections Reset on Startup
# ═══════════════════════════════════════════════════════════════════════════

class TestT10_StaleConnectionReset:
    """
    After a crash, active_connections can be stale (e.g., 50).
    Startup recovery or graceful_stop must reset them to 0.
    """

    def test_stale_counters_detected(self, redis_client):
        """Verify the problem: stale counter prevents proxy selection."""
        addr_stale = "stale.proxy:8080"
        addr_fresh = "fresh.proxy:8080"

        redis_client.sadd("Proxy:ActiveList", addr_stale)
        redis_client.sadd("Proxy:ActiveList", addr_fresh)
        redis_client.hset(f"Proxy:Metrics:{addr_stale}", mapping={
            "active_connections": 50, "status": "Active"
        })
        redis_client.hset(f"Proxy:Metrics:{addr_fresh}", mapping={
            "active_connections": 0, "status": "Active"
        })

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

            # With least-connections, stale proxy should never be selected
            chosen = pm.get_proxy(strategy="least-connections")
            assert chosen == addr_fresh, f"Stale proxy selected: {chosen}"

    def test_manual_counter_reset(self, redis_client):
        """Verify that resetting active_connections fixes the problem."""
        addr = "stale.proxy:8080"
        redis_client.sadd("Proxy:ActiveList", addr)
        redis_client.hset(f"Proxy:Metrics:{addr}", mapping={
            "active_connections": 50, "status": "Active"
        })

        # Simulate startup recovery reset
        for proxy_addr in redis_client.smembers("Proxy:ActiveList"):
            redis_client.hset(f"Proxy:Metrics:{proxy_addr}", "active_connections", 0)

        val = int(redis_client.hget(f"Proxy:Metrics:{addr}", "active_connections"))
        assert val == 0, "Startup recovery failed to reset counter"
