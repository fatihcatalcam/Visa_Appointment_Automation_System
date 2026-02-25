import sqlite3
import os
import json
import base64
import logging
import psycopg2
import psycopg2.extras

from config.pg_adapter import pg_manager

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'users.db')
SECRET_KEY_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', '.secret_key')

# ════════════════════════════════════════════════════════════════════════════
# Fernet Encryption (AES-128-CBC) — replaces old Base64 "encoding"
# ════════════════════════════════════════════════════════════════════════════
_fernet = None

def _get_fernet():
    """Lazy-init Fernet cipher. Creates a key on first run."""
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
        os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
        if os.path.exists(SECRET_KEY_PATH):
            with open(SECRET_KEY_PATH, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(SECRET_KEY_PATH, 'wb') as f:
                f.write(key)
            logger.info("🔑 Yeni şifreleme anahtarı oluşturuldu.")
        _fernet = Fernet(key)
    except ImportError:
        logger.warning("cryptography kütüphanesi yok — Base64 fallback kullanılıyor.")
        _fernet = None
    return _fernet

def _encrypt(text: str) -> str:
    """Encrypt a plaintext string (Fernet > Base64 fallback)"""
    if not text: return ""
    f = _get_fernet()
    if f:
        try:
            return f.encrypt(text.encode('utf-8')).decode('utf-8')
        except Exception:
            pass
    # Fallback: Base64
    return base64.b64encode(text.encode('utf-8')).decode('utf-8')

def _decrypt(text: str) -> str:
    """Decrypt a ciphertext string (tries Fernet first, then Base64 for legacy data)"""
    if not text: return ""
    f = _get_fernet()
    if f:
        try:
            return f.decrypt(text.encode('utf-8')).decode('utf-8')
        except Exception:
            pass
    # Fallback: try Base64 for legacy passwords
    try:
        return base64.b64decode(text.encode('utf-8')).decode('utf-8')
    except Exception:
        return text

# Legacy aliases so existing imports don't break
_simple_encode = _encrypt
_simple_decode = _decrypt

# ════════════════════════════════════════════════════════════════════════════
# DB Initialization 
# ════════════════════════════════════════════════════════════════════════════

def init_db():
    """Veritabanı tablosunu oluştur (Yoksa)"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_active BOOLEAN DEFAULT 1,
            email TEXT UNIQUE NOT NULL,
            password_enc TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            jurisdiction TEXT,
            location TEXT,
            category TEXT,
            appointment_for TEXT DEFAULT 'Individual',
            visa_type TEXT,
            visa_sub_type TEXT,
            proxy_address TEXT,
            check_interval INTEGER DEFAULT 60,
            minimum_days INTEGER DEFAULT 0,
            headless BOOLEAN DEFAULT 1,
            is_scout BOOLEAN DEFAULT 0,
            auto_book BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'Idle',
            last_check TEXT,
            check_count INTEGER DEFAULT 0,
            error_msg TEXT,
            cooldown_until TEXT,
            worker_state TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'Active',
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            consecutive_fails INTEGER DEFAULT 0,
            last_used TEXT,
            disabled_until TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Veritabanı başlatıldı.")

# ════════════════════════════════════════════════════════════════════════════
# Helper: PostgreSQL Type Safety
# ════════════════════════════════════════════════════════════════════════════

def _pg_type_safety(user_data):
    """PostgreSQL BOOLEAN sütunlarını Python bool'a dönüştürür."""
    for bool_key in ('headless', 'is_active', 'is_scout', 'auto_book'):
        if bool_key in user_data:
            user_data[bool_key] = bool(user_data[bool_key])
    return user_data

# ════════════════════════════════════════════════════════════════════════════
# CRUD Operations
# ════════════════════════════════════════════════════════════════════════════

def get_all_users():
    """Tüm kullanıcıları getir"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users")
                return [dict(row) for row in cur.fetchall()]
        finally:
            pg_manager.release_connection(conn_pg)
               
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

def get_active_users():
    """Sadece aktif (çalıştırılacak) kullanıcıları getir"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE is_active=TRUE")
                return [dict(row) for row in cur.fetchall()]
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE is_active=1")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

def get_user_by_id(user_id):
    """ID'ye göre tek bir kullanıcı getir"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_or_update_user(user_data):
    """Kullanıcı ekle veya güncelle"""
    if 'password' in user_data and user_data['password']:
        user_data['password_enc'] = _encrypt(user_data.pop('password'))
        
    _pg_type_safety(user_data)
        
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                if 'id' in user_data and user_data['id'] is not None:
                    user_id = user_data.pop('id')
                    columns = ', '.join(f"{k} = %s" for k in user_data.keys())
                    values = list(user_data.values())
                    values.append(user_id)
                    cur.execute(f"UPDATE users SET {columns} WHERE id=%s", values)
                else:
                    columns = ', '.join(user_data.keys())
                    placeholders = ', '.join('%s' for _ in user_data)
                    values = list(user_data.values())
                    cur.execute(f"INSERT INTO users ({columns}) VALUES ({placeholders})", values)
            return
        except Exception as e:
            logger.error(f"PostgreSQL Add/Update User Error: {e}")
        finally:
            pg_manager.release_connection(conn_pg)

    # SQLite Fallback           
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if 'id' in user_data and user_data['id'] is not None:
        user_id = user_data.pop('id')
        columns = ', '.join(f"{k} = ?" for k in user_data.keys())
        values = list(user_data.values())
        values.append(user_id)
        cursor.execute(f"UPDATE users SET {columns} WHERE id=?", values)
    else:
        columns = ', '.join(user_data.keys())
        placeholders = ', '.join('?' for _ in user_data)
        values = list(user_data.values())
        cursor.execute(f"INSERT INTO users ({columns}) VALUES ({placeholders})", values)
        
    conn.commit()
    conn.close()

def bulk_add_users(users_list):
    """Excel vb. toplu aktarımlar için birden fazla kullanıcı ekle"""
    if not users_list:
        return
        
    processed_list = []
    headers = None
    
    for user_data in users_list:
        if 'password' in user_data and user_data['password']:
            user_data['password_enc'] = _encrypt(user_data.pop('password'))
        _pg_type_safety(user_data)
            
        if not headers:
            headers = list(user_data.keys())
            
        processed_list.append([user_data.get(k) for k in headers])

    if not headers: return

    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                columns = ', '.join(headers)
                placeholders = ', '.join('%s' for _ in headers)
                query = f"INSERT INTO users ({columns}) VALUES ({placeholders})"
                cur.executemany(query, processed_list)
            return
        except Exception as e:
            logger.error(f"PostgreSQL Bulk Add Error: {e}")
        finally:
            pg_manager.release_connection(conn_pg)

    # SQLite Fallback           
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    columns = ', '.join(headers)
    placeholders = ', '.join('?' for _ in headers)
    query = f"INSERT INTO users ({columns}) VALUES ({placeholders})"
    cursor.executemany(query, processed_list)
    conn.commit()
    conn.close()

def update_user_status(user_id, status, error_msg="", last_check=None):
    """Arayüzde gösterilecek canlı durumu güncelle"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                query = "UPDATE users SET status=%s, error_msg=%s"
                params = [status, error_msg]
                if last_check:
                    query += ", last_check=%s, check_count = check_count + 1"
                    params.append(last_check)
                query += " WHERE id=%s"
                params.append(user_id)
                cur.execute(query, params)
            return
        finally:
            pg_manager.release_connection(conn_pg)

    # SQLite Fallback           
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = "UPDATE users SET status=?, error_msg=?"
    params = [status, error_msg]
    
    if last_check:
        query += ", last_check=?, check_count = check_count + 1"
        params.append(last_check)
        
    query += " WHERE id=?"
    params.append(user_id)
    
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def clear_user_cooldown(user_id):
    """Kullanıcının üzerindeki cooldown engelini (manuel/otomatik) kaldırır"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("UPDATE users SET cooldown_until=NULL, status='Bekliyor', error_msg='' WHERE id=%s", (user_id,))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET cooldown_until=NULL, status='Bekliyor', error_msg='' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def delete_user(user_id):
    """Kullanıcıyı sil"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def get_global_setting(key, default=""):
    """Global bir ayar getir"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("SELECT value FROM global_settings WHERE key=%s", (key,))
                row = cur.fetchone()
                return row[0] if row else default
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM global_settings WHERE key=?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def set_global_setting(key, value):
    """Global bir ayarı kaydet"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute(
                    "INSERT INTO global_settings (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", 
                    (key, str(value))
                )
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO global_settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# ════════════════════════════════════════════════════════════════════════════
# Proxy Management DB Helpers
# ════════════════════════════════════════════════════════════════════════════

def get_all_proxies():
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM proxies")
                return [dict(row) for row in cur.fetchall()]
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proxies")
    proxies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return proxies

def add_proxy(address):
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("INSERT INTO proxies (address) VALUES (%s) ON CONFLICT DO NOTHING", (address,))
            return
        except Exception as e:
            logger.error(f"Error adding proxy to PG: {e}")
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO proxies (address) VALUES (?)", (address,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error adding proxy: {e}")
    finally:
        conn.close()

def update_proxy_stats(address, success=True):
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                if success:
                    cur.execute('''
                        UPDATE proxies 
                        SET success_count = success_count + 1,
                            consecutive_fails = 0,
                            status = 'Active',
                            last_used = %s
                        WHERE address = %s
                    ''', (now_str, address))
                else:
                    cur.execute("SELECT consecutive_fails FROM proxies WHERE address = %s", (address,))
                    row = cur.fetchone()
                    fails = (row[0] if (row and row[0] is not None) else 0) + 1
                    
                    status = 'Active'
                    disabled_until = None
                    if fails >= 5:
                        status = 'Disabled'
                        disabled_until = (datetime.datetime.now() + datetime.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
                        
                    cur.execute('''
                        UPDATE proxies 
                        SET fail_count = fail_count + 1,
                            consecutive_fails = %s,
                            status = %s,
                            disabled_until = %s,
                            last_used = %s
                        WHERE address = %s
                    ''', (fails, status, disabled_until, now_str, address))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    # SQLite Fallback           
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if success:
        cursor.execute('''
            UPDATE proxies 
            SET success_count = success_count + 1,
                consecutive_fails = 0,
                status = 'Active',
                last_used = ?
            WHERE address = ?
        ''', (now_str, address))
    else:
        cursor.execute("SELECT consecutive_fails FROM proxies WHERE address = ?", (address,))
        row = cursor.fetchone()
        fails = (row[0] if (row and row[0] is not None) else 0) + 1
        
        status = 'Active'
        disabled_until = None
        if fails >= 5:
            status = 'Disabled'
            disabled_until = (datetime.datetime.now() + datetime.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
            
        cursor.execute('''
            UPDATE proxies 
            SET fail_count = fail_count + 1,
                consecutive_fails = ?,
                status = ?,
                disabled_until = ?,
                last_used = ?
            WHERE address = ?
        ''', (fails, status, disabled_until, now_str, address))
    
    conn.commit()
    conn.close()

def set_user_cooldown(user_id, hours=4, reason="Risk Limit Reached"):
    import datetime
    cooldown_time = (datetime.datetime.now() + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("UPDATE users SET cooldown_until = %s, status = 'Cooldown', error_msg = %s WHERE id = %s", (cooldown_time, reason, user_id))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET cooldown_until = ?, status = 'Cooldown', error_msg = ? WHERE id = ?", (cooldown_time, reason, user_id))
    conn.commit()
    conn.close()

# ════════════════════════════════════════════════════════════════════════════
# Worker State Persistence (A5 — Crash Recovery)
# ════════════════════════════════════════════════════════════════════════════

def save_worker_state(user_id, state_dict):
    """Worker thread durumunu JSON olarak kaydet"""
    state_json = json.dumps(state_dict)
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("UPDATE users SET worker_state = %s WHERE id = %s", (state_json, user_id))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET worker_state = ? WHERE id = ?", (state_json, user_id))
    conn.commit()
    conn.close()

def get_worker_state(user_id):
    """Kayıtlı worker state'i oku (çökme sonrası kurtarma için)"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("SELECT worker_state FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        finally:
            pg_manager.release_connection(conn_pg)
        return None

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT worker_state FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return json.loads(row[0])
    return None

def clear_worker_state(user_id):
    """Worker state'i temizle (normal kapanıştan sonra)"""
    conn_pg = pg_manager.get_connection()
    if conn_pg:
        try:
            with conn_pg.cursor() as cur:
                cur.execute("UPDATE users SET worker_state = NULL WHERE id = %s", (user_id,))
            return
        finally:
            pg_manager.release_connection(conn_pg)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET worker_state = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

# ════════════════════════════════════════════════════════════════════════════
# Lua Script: Atomic Account Risk Scoring (Redis)
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
    """Atomically increases the risk score of an account in Redis."""
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
    """Returns the current risk score (0-100)"""
    from config.cache import redis_manager
    r = redis_manager.get_client()
    if not redis_manager.is_connected or not r: return 0
    val = r.get(f"Account:Risk:{user_id}")
    return int(val) if val else 0

# ════════════════════════════════════════════════════════════════════════════
# Migrations (SQLite sütun ekleme — mevcut DB'ler için)
# ════════════════════════════════════════════════════════════════════════════

def run_db_migrations():
    """Mevcut SQLite tabloya yeni sütunları ekle."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    migrations = [
        "ALTER TABLE users ADD COLUMN cooldown_until TEXT",
        "ALTER TABLE users ADD COLUMN is_scout BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN auto_book BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN worker_state TEXT",
    ]
    for sql in migrations:
        try:
            cursor.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Zaten var
    conn.close()

# İçe aktarıldığında veritabanını hazırla
init_db()
run_db_migrations()
