"""
Shared test fixtures for comp-bot production reliability tests.
Uses fakeredis with Lua support — no real Redis needed.
"""
import pytest
import sys
import os
import threading

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import fakeredis


@pytest.fixture
def redis_client():
    """Fresh fakeredis instance with Lua support for each test."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.flushall()
    client.close()


@pytest.fixture
def populated_redis(redis_client):
    """Redis pre-loaded with 5 test proxies in ActiveList."""
    proxies = [
        "1.1.1.1:8080",
        "2.2.2.2:8080",
        "3.3.3.3:8080",
        "4.4.4.4:8080",
        "5.5.5.5:8080",
    ]
    for addr in proxies:
        redis_client.sadd("Proxy:ActiveList", addr)
        redis_client.hset(f"Proxy:Metrics:{addr}", mapping={
            "success_count": 0,
            "fail_count": 0,
            "consecutive_fails": 0,
            "active_connections": 0,
            "average_latency_ms": 0,
            "status": "Active",
        })
    return redis_client, proxies


@pytest.fixture
def lua_proxy_fail(redis_client):
    """Registered Lua proxy failure script on the given client."""
    from bot.proxy_manager import LUA_PROXY_FAIL
    script = redis_client.register_script(LUA_PROXY_FAIL)
    return script


@pytest.fixture
def lua_risk_score(redis_client):
    """Registered Lua risk score script on the given client."""
    # Import the script text from database.py
    from config.database import LUA_RISK_SCORE
    script = redis_client.register_script(LUA_RISK_SCORE)
    return script
