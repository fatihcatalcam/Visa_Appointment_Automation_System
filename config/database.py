import os
import json
import base64
import logging
import datetime

from data.repositories import UserRepository, ProxyRepository, GlobalSettingsRepository
from data.database import db

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'users.db') # Left for legacy path references if any
SECRET_KEY_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', '.secret_key')

from config.security import _encrypt, _decrypt, _simple_encode, _simple_decode

# ════════════════════════════════════════════════════════════════════════════
# Legacy Setup
# ════════════════════════════════════════════════════════════════════════════

def init_db():
    logger.info("Veritabanı başlatıldı (PostgreSQL Repository mimarisi üzerinden çalışıyor).")
    # Postgres tablosu db._init_pg() içinde ensure_tables() ile kuruluyor, ek bir şeye gerek yok.

def run_db_migrations():
    """No longer required for strictly PostgreSQL since it creates all columns in ensure_tables"""
    pass

def _pg_type_safety(user_data):
    for bool_key in ('headless', 'is_active', 'is_scout', 'auto_book'):
        if bool_key in user_data:
            user_data[bool_key] = bool(user_data[bool_key])
    return user_data

# ════════════════════════════════════════════════════════════════════════════
# Users
# ════════════════════════════════════════════════════════════════════════════

def get_all_users():
    return UserRepository.get_all()

def get_active_users():
    return UserRepository.get_active()

def get_user_by_id(user_id):
    return UserRepository.get_by_id(user_id)

def add_or_update_user(user_data):
    _pg_type_safety(user_data)
    if 'id' in user_data and user_data['id']:
        user_id = user_data.pop('id')
        UserRepository.update(user_id, user_data)
    else:
        UserRepository.create(user_data)

def bulk_add_users(users_list):
    for u in users_list:
        add_or_update_user(u)

def update_user_status(user_id, status, error_msg="", last_check=None):
    UserRepository.update_status(user_id, status, error_msg, last_check)

def clear_user_cooldown(user_id):
    UserRepository.update(user_id, {"cooldown_until": None, "status": "Bekliyor", "error_msg": ""})

def clear_all_cooldowns():
    """Clears cooldown state for ALL users at once."""
    from data.database import db
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET cooldown_until = NULL, status = 'Bekliyor', error_msg = '' WHERE cooldown_until IS NOT NULL")
            return cur.rowcount
    finally:
        db.release_connection(conn)

def delete_user(user_id):
    UserRepository.delete(user_id)

def set_user_cooldown(user_id, hours=4, reason="Risk Limit Reached"):
    dt = (datetime.datetime.now() + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    UserRepository.set_cooldown(user_id, dt)
    UserRepository.update_status(user_id, status="Cooldown", error_msg=reason)

# ════════════════════════════════════════════════════════════════════════════
# Global Settings
# ════════════════════════════════════════════════════════════════════════════

def get_global_setting(key, default=""):
    return GlobalSettingsRepository.get(key, default)

def set_global_setting(key, value):
    GlobalSettingsRepository.set(key, value)

# ════════════════════════════════════════════════════════════════════════════
# Proxies
# ════════════════════════════════════════════════════════════════════════════

def get_all_proxies():
    return ProxyRepository.get_all()

def add_proxy(address):
    ProxyRepository.create(address)

def update_proxy_stats(address, success=True):
    if success:
        ProxyRepository.update_proxy_status(address, "Active", success_increment=1, consecutive_fails=0)
    else:
        # For simple fallback without Lua:
        # We can't strictly atomically check fails=5 here without a fetch, but proxy manager now does this anyway.
        # This function is here just for backwards compatibility if GUI calls it directly.
        pass

# ════════════════════════════════════════════════════════════════════════════
# Worker State Persistence
# ════════════════════════════════════════════════════════════════════════════

def save_worker_state(user_id, state_dict):
    UserRepository.update_worker_state(user_id, json.dumps(state_dict))

def get_worker_state(user_id):
    u = UserRepository.get_by_id(user_id)
    if u and u.get("worker_state"):
        try:
            return json.loads(u["worker_state"])
        except Exception:
            return None
    return None

def clear_worker_state(user_id):
    UserRepository.update_worker_state(user_id, None)

# ════════════════════════════════════════════════════════════════════════════
# Risk Engine (Redis wrapper)
# ════════════════════════════════════════════════════════════════════════════

LUA_RISK_SCORE = """
local key = KEYS[1]
local points = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local reset_to = tonumber(ARGV[3])

local score = redis.call('INCRBY', key, points)
redis.call('EXPIRE', key, 86400)

if score >= threshold then
    redis.call('SET', key, reset_to)
    redis.call('EXPIRE', key, 86400)
    return score
end
return 0
"""

_lua_risk_script = None

def report_account_risk(user_id, points, reason="General Risk"):
    global _lua_risk_script
    from config.cache import redis_manager
    r = redis_manager.get_client()
    
    if not redis_manager.is_connected or not r:
        if points >= 30:
            set_user_cooldown(user_id, hours=1, reason=reason)
        return

    if _lua_risk_script is None:
        _lua_risk_script = r.register_script(LUA_RISK_SCORE)

    try:
        result = _lua_risk_script(
            keys=[f"Account:Risk:{user_id}"],
            args=[points, 70, 35]
        )
        
        current_score = int(r.get(f"Account:Risk:{user_id}") or 0)
        logger.warning(f"⚠️ Risk Artışı [User {user_id}]: +{points} Puan ({reason}). Güncel Risk: {current_score}/100")
        
        if result:
            logger.error(f"🛑 KRİTİK RİSK [User {user_id}]: Puan {result}. Hesap 12 saatliğine Karantinaya alınıyor! ({reason})")
            set_user_cooldown(user_id, hours=12, reason="Karantina (Risk Yüksek)")
    except Exception as e:
        logger.error(f"Lua risk script error: {e}")
        key = f"Account:Risk:{user_id}"
        current_score = r.incrby(key, points)
        r.expire(key, 86400)
        if current_score >= 70:
            set_user_cooldown(user_id, hours=12, reason="Karantina (Risk Yüksek)")
            r.set(key, 35)
            r.expire(key, 86400)

def get_account_risk(user_id):
    from config.cache import redis_manager
    r = redis_manager.get_client()
    if not redis_manager.is_connected or not r: return 0
    val = r.get(f"Account:Risk:{user_id}")
    return int(val) if val else 0
