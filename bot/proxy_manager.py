import threading
import time
import datetime
import logging
from data.repositories import ProxyRepository
from config.cache import redis_manager

logger = logging.getLogger(__name__)

# --- Lua Script: Atomic Proxy Failure ---
# Eliminates race condition where two threads both read cf=4 and both disable.
# Everything happens in a single Redis command — no interleaving possible.
LUA_PROXY_FAIL = """
local key = KEYS[1]
local active_list = KEYS[2]
local cooldown_queue = KEYS[3]
local general_threshold = tonumber(ARGV[1])
local cooldown_secs = tonumber(ARGV[2])
local error_type = ARGV[3]
local address = ARGV[4]
local now = tonumber(ARGV[5])

-- Increment counters
local cf = redis.call('HINCRBY', key, 'consecutive_fails', 1)
redis.call('HINCRBY', key, 'fail_count', 1)

-- Track error subtype
if error_type == '403' then
    redis.call('HINCRBY', key, 'fail_count_403', 1)
elseif error_type == '429' then
    redis.call('HINCRBY', key, 'fail_count_429', 1)
elseif error_type == 'timeout' then
    redis.call('HINCRBY', key, 'fail_count_timeout', 1)
end

-- Determine threshold (403 is stricter)
local threshold = general_threshold
if error_type == '403' then threshold = 2 end

-- Check if threshold crossed
if cf >= threshold then
    redis.call('SREM', active_list, address)
    redis.call('ZADD', cooldown_queue, now + cooldown_secs, address)
    redis.call('HSET', key, 'status', 'Disabled')
    return 1
end
return 0
"""

class ProxyManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ProxyManager, cls).__new__(cls)
                cls._instance._init_manager()
            return cls._instance

    def _init_manager(self):
        self.proxies = []
        self._lua_proxy_fail = None  # Will be registered on first use
        self.load_proxies_from_db()
        self.last_reload = time.time()
        self.reload_interval = 300 # Reload from DB every 5 mins

    def load_proxies_from_db(self):
        try:
            db_proxies = ProxyRepository.get_all()
            self.proxies = db_proxies
            logger.info(f"Loaded {len(self.proxies)} proxies from database.")
            
            # Sync to Redis if available
            r = redis_manager.get_client()
            if redis_manager.is_connected and r:
                for p in self.proxies:
                    addr = p['address']
                    key = f"Proxy:Metrics:{addr}"
                    # Don't overwrite existing metrics if they exist
                    if not r.exists(key):
                        r.hset(key, mapping={
                            "success_count": p.get("success_count", 0),
                            "fail_count": p.get("fail_count", 0),
                            "consecutive_fails": p.get("consecutive_fails", 0),
                            "status": p.get("status", "Active")
                        })
                    
                    if p.get("status") == "Active":
                        r.sadd("Proxy:ActiveList", addr)
                    elif p.get("status") == "Disabled" and not r.zscore("Proxy:CooldownQueue", addr):
                        # Give it a default cooldown if missing (e.g. +30 mins)
                        expire_time = time.time() + 1800 
                        if p.get("disabled_until"):
                            try:
                                dt = datetime.datetime.strptime(p['disabled_until'], "%Y-%m-%d %H:%M:%S")
                                expire_time = dt.timestamp()
                            except: pass
                        r.zadd("Proxy:CooldownQueue", {addr: expire_time})
        except Exception as e:
            logger.error(f"Failed to load proxies from DB/Redis: {e}")

    def import_proxy_list(self, proxy_list):
        """Imports a list of proxies (strings) into the database."""
        for p in proxy_list:
            if p.strip():
                ProxyRepository.create(p.strip())
        self.load_proxies_from_db()

    def _is_proxy_available(self, proxy_dict):
        status = proxy_dict.get('status', 'Active')
        if status == 'Active':
            return True
            
        if status == 'Disabled':
            disabled_until_str = proxy_dict.get('disabled_until')
            if not disabled_until_str:
                return False
                
            try:
                disabled_until = datetime.datetime.strptime(disabled_until_str, "%Y-%m-%d %H:%M:%S")
                if datetime.datetime.now() > disabled_until:
                    # Cooldown expired, we can try it again
                    return True
            except Exception:
                pass
                
        return False

    def _check_cooldowns(self, r):
        """Moves expired proxies from CooldownQueue back to ActiveList in Redis"""
        if not r: return
        now = time.time()
        # Get all proxies where their cooldown timestamp is <= now
        expired = r.zrangebyscore("Proxy:CooldownQueue", 0, now)
        for addr in expired:
            r.zrem("Proxy:CooldownQueue", addr)
            r.sadd("Proxy:ActiveList", addr)
            r.hset(f"Proxy:Metrics:{addr}", "consecutive_fails", 0)
            r.hset(f"Proxy:Metrics:{addr}", "status", "Active")
            logger.info(f"🔄 Proxy Cooldown süresi doldu, tekrar ActiveList'e alındı: {addr}")

    def get_proxy(self, strategy="round-robin", assigned_proxy=None):
        r = redis_manager.get_client()
        
        if redis_manager.is_connected and r:
            self._check_cooldowns(r)
            
            if assigned_proxy and r.sismember("Proxy:ActiveList", assigned_proxy):
                r.hincrby(f"Proxy:Metrics:{assigned_proxy}", "active_connections", 1)
                return assigned_proxy
                
            active_proxies = r.smembers("Proxy:ActiveList")
            if not active_proxies:
                logger.warning("Redis Proxy:ActiveList BOŞ! Hiç sağlıklı proxy yok.")
                return None
                
            if strategy == "least-connections":
                # Least Connections Algorithm
                # Pull active_connections for all available proxies
                best_proxy = None
                min_conn = float('inf')
                for p in active_proxies:
                    conn = int(r.hget(f"Proxy:Metrics:{p}", "active_connections") or 0)
                    if conn < min_conn:
                        min_conn = conn
                        best_proxy = p
                
                if best_proxy:
                    r.hincrby(f"Proxy:Metrics:{best_proxy}", "active_connections", 1)
                    return best_proxy

            # Fallback / Round-Robin (Random is effectively round-robin over time at scale)
            chosen = r.srandmember("Proxy:ActiveList")
            if chosen:
                r.hincrby(f"Proxy:Metrics:{chosen}", "active_connections", 1)
                return chosen
                
        # --- Fallback to SQLite Logic if Redis is down ---
        if time.time() - self.last_reload > self.reload_interval:
            self.load_proxies_from_db()
            self.last_reload = time.time()

        if not self.proxies:
            return None

        available_proxies = [p for p in self.proxies if self._is_proxy_available(p)]
        
        if not available_proxies:
            logger.warning("No AVAILABLE proxies! All proxies are disabled or dead.")
            return None

        if assigned_proxy:
            for p in available_proxies:
                if p['address'] == assigned_proxy:
                    return p['address']
            logger.warning(f"Assigned proxy {assigned_proxy} is dead/disabled. Assigning a new one.")

        available_proxies.sort(key=lambda x: (x.get('consecutive_fails', 0), x.get('last_used', '')))
        
        selected = available_proxies[0]['address']
        return selected

    def report_success(self, address):
        if not address: return
        r = redis_manager.get_client()
        if redis_manager.is_connected and r:
            key = f"Proxy:Metrics:{address}"
            r.hincrby(key, "success_count", 1)
            r.hset(key, "consecutive_fails", 0)
            
        # Instead of generic update_proxy_stats, use ProxyRepository
        def _bg_success():
            ProxyRepository.update_proxy_status(address, "Active", success_increment=1, consecutive_fails=0)
        threading.Thread(target=_bg_success, daemon=True).start()

    def report_failure(self, address, error_type="general"):
        if not address: return
        r = redis_manager.get_client()
        if redis_manager.is_connected and r:
            # Register Lua script on first use (lazy init)
            if self._lua_proxy_fail is None:
                self._lua_proxy_fail = r.register_script(LUA_PROXY_FAIL)
            
            key = f"Proxy:Metrics:{address}"
            try:
                disabled = self._lua_proxy_fail(
                    keys=[key, "Proxy:ActiveList", "Proxy:CooldownQueue"],
                    args=[5, 1800, error_type, address, int(time.time())]
                )
                if disabled:
                    logger.error(f"🔴 ATOMIC: Proxy {address} eşiği aştı ({error_type}). 30dk cooldown.")
                    # P2: Cascading disable circuit breaker
                    self._check_circuit_breaker(r)
            except Exception as e:
                logger.error(f"Lua script error: {e}")
                # Fallback to non-atomic if Lua fails (shouldn't happen)
                r.hincrby(key, "fail_count", 1)
                r.hincrby(key, "consecutive_fails", 1)

        def _bg_fail(was_disabled=bool(disabled) if 'disabled' in dir() else False):
            # If it was disabled by Lua, pass that to Postgres
            if was_disabled:
                dt = (datetime.datetime.now() + datetime.timedelta(seconds=1800)).strftime("%Y-%m-%d %H:%M:%S")
                ProxyRepository.update_proxy_status(address, "Disabled", fail_increment=1, consecutive_fails=5, disabled_until=dt)
            else:
                ProxyRepository.update_proxy_status(address, "Active", fail_increment=1)
                
        threading.Thread(target=_bg_fail, daemon=True).start()

    def _check_circuit_breaker(self, r):
        """P2: If >60% proxies are disabled, it's a site-wide issue — re-enable all."""
        try:
            active_count = r.scard("Proxy:ActiveList")
            cooldown_count = r.zcard("Proxy:CooldownQueue")
            total = active_count + cooldown_count
            
            if total == 0:
                return
            
            disabled_ratio = cooldown_count / total
            if disabled_ratio > 0.6:
                logger.critical(
                    f"🚨 CIRCUIT BREAKER: {cooldown_count}/{total} proxy devre dışı (>{int(disabled_ratio*100)}%). "
                    f"Site çapında sorun şüphesi — tüm proxy'ler yeniden etkinleştiriliyor!"
                )
                # Move all cooldown proxies back to active
                cooldown_members = r.zrangebyscore("Proxy:CooldownQueue", 0, float('inf'))
                for addr in cooldown_members:
                    r.zrem("Proxy:CooldownQueue", addr)
                    r.sadd("Proxy:ActiveList", addr)
                    r.hset(f"Proxy:Metrics:{addr}", "consecutive_fails", 0)
                    r.hset(f"Proxy:Metrics:{addr}", "status", "Active")
                logger.info(f"✅ Circuit breaker: {len(cooldown_members)} proxy yeniden etkinleştirildi.")
        except Exception as e:
            logger.error(f"Circuit breaker kontrolü hatası: {e}")

    def report_release(self, address):
        """Decrements the active connections counter."""
        if not address: return
        r = redis_manager.get_client()
        if redis_manager.is_connected and r:
            key = f"Proxy:Metrics:{address}"
            current = int(r.hget(key, "active_connections") or 0)
            if current > 0:
                r.hincrby(key, "active_connections", -1)

    def report_latency(self, address, latency_ms):
        """Updates the rolling average latency of the proxy."""
        if not address: return
        r = redis_manager.get_client()
        if redis_manager.is_connected and r:
            key = f"Proxy:Metrics:{address}"
            curr_avg = float(r.hget(key, "average_latency_ms") or 0)
            # Simple exponential moving average (alpha = 0.2)
            new_avg = (0.2 * latency_ms) + (0.8 * curr_avg) if curr_avg > 0 else latency_ms
            r.hset(key, "average_latency_ms", new_avg)
            
            # If latency is absurdly high (> 8000ms), penalize it
            if new_avg > 8000:
                self.report_failure(address, error_type="timeout")

# Global singleton instance
proxy_manager = ProxyManager()
