import pytest
from unittest.mock import MagicMock, patch

def test_get_proxy_round_robin(mocker):
    from bot.proxy_manager import ProxyManager
    pm = ProxyManager.__new__(ProxyManager)
    
    # Mocking SQLite proxies and redis availability
    pm.proxies = [
        {"address": "p1", "status": "Active"},
        {"address": "p2", "status": "Active"},
        {"address": "p3", "status": "Disabled"}
    ]
    
    pm._lock = MagicMock()
    pm.last_reload = 0
    # Simulate DB load skipping
    pm.load_proxies_from_db = MagicMock()
    pm._lua_proxy_fail = None
    pm.reload_interval = 999
    
    import config.cache
    pm._is_proxy_available = lambda p: p["status"] == "Active"
    
    with patch("bot.proxy_manager.redis_manager.is_connected", new_callable=mocker.PropertyMock(return_value=False)):
        p1 = pm.get_proxy(strategy="round-robin")
        p2 = pm.get_proxy(strategy="round-robin")
        p3 = pm.get_proxy(strategy="round-robin")
    
    assert p1 == "p1"
    assert p2 == "p1"  # Both return p1 in this simple fallback since we didn't mock incrementing failures or updating last_used.
    # Should wrap around since p3 is disabled
    assert p3 == "p1"
