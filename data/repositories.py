from typing import List, Dict, Optional, Any
import psycopg2.extras
from data.database import db
import logging
from config.security import _encrypt, _decrypt

logger = logging.getLogger(__name__)

class UserRepository:
    @staticmethod
    def get_all() -> List[Dict]:
        conn = db.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users")
                users = [dict(row) for row in cur.fetchall()]
                for u in users:
                    if u.get('email_app_password'):
                        try: u['email_app_password'] = _decrypt(u['email_app_password'])
                        except: pass
                return users
        finally:
            db.release_connection(conn)

    @staticmethod
    def get_active() -> List[Dict]:
        conn = db.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE is_active=TRUE")
                users = [dict(row) for row in cur.fetchall()]
                for u in users:
                    if u.get('email_app_password'):
                        try: u['email_app_password'] = _decrypt(u['email_app_password'])
                        except: pass
                return users
        finally:
            db.release_connection(conn)

    @staticmethod
    def get_by_id(user_id: int) -> Optional[Dict]:
        conn = db.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
                row = cur.fetchone()
                if row:
                    user_dict = dict(row)
                    if user_dict.get('email_app_password'):
                        try: user_dict['email_app_password'] = _decrypt(user_dict['email_app_password'])
                        except: pass
                    return user_dict
                return None
        finally:
            db.release_connection(conn)

    @staticmethod
    def create(user_data: Dict) -> int:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                # Add default fields if they don't exist
                # encrypt password if it exists
                pwd = user_data.get('password', '')
                enc_pwd = _encrypt(pwd) if pwd else user_data.get('password_enc', '')
                
                enc_email_pwd = ''
                email_pwd = user_data.get('email_app_password', '')
                if email_pwd:
                    enc_email_pwd = _encrypt(email_pwd)
                
                cur.execute(
                    """INSERT INTO users (is_active, email, password_enc, first_name, last_name, phone, jurisdiction, 
                                          location, category, appointment_for, visa_type, visa_sub_type, proxy_address, 
                                          check_interval, minimum_days, headless, is_scout, auto_book, status, last_check, check_count, 
                                          error_msg, cooldown_until, email_app_password, travel_date, max_appointment_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id
                    """,
                    (
                        bool(user_data.get('is_active', True)), user_data['email'], enc_pwd,
                        user_data.get('first_name'), user_data.get('last_name'), user_data.get('phone'),
                        user_data.get('jurisdiction'), user_data.get('location'), user_data.get('category'),
                        user_data.get('appointment_for', 'Individual'), user_data.get('visa_type'),
                        user_data.get('visa_sub_type'), user_data.get('proxy_address'),
                        user_data.get('check_interval', 60), user_data.get('minimum_days', 0),
                        bool(user_data.get('headless', True)), bool(user_data.get('is_scout', False)),
                        bool(user_data.get('auto_book', False)), user_data.get('status', 'Idle'),
                        user_data.get('last_check'), user_data.get('check_count', 0),
                        user_data.get('error_msg'), user_data.get('cooldown_until'),
                        enc_email_pwd, user_data.get('travel_date', ''),
                        user_data.get('max_appointment_date', '')
                    )
                )
                return cur.fetchone()[0]
        finally:
            db.release_connection(conn)
            
    @staticmethod
    def update(user_id: int, user_data: Dict) -> bool:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                updates = []
                values = []
                for k, v in user_data.items():
                    if k == 'password':
                        updates.append("password_enc = %s")
                        values.append(_encrypt(v))
                        continue
                    if k == 'email_app_password':
                        updates.append("email_app_password = %s")
                        values.append(_encrypt(v) if v else '')
                        continue
                    if k in ('id', 'password_enc'): continue
                    updates.append(f"{k} = %s")
                    values.append(v)
                
                if not updates: return True
                
                values.append(user_id)
                cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(values))
                return cur.rowcount > 0
        finally:
            db.release_connection(conn)

    @staticmethod
    def update_status(user_id: int, status: str = None, error_msg: str = None, last_check: str = None):
        """Sadece durum güncellemeleri yapan helper (Manager.py kolaylığı için)"""
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                updates = []
                args = []
                if status is not None:
                    updates.append("status = %s")
                    args.append(status)
                if error_msg is not None:
                    updates.append("error_msg = %s")
                    args.append(error_msg[:255])
                if last_check is not None:
                    updates.append("last_check = %s")
                    args.append(last_check)
                
                if not updates: return
                
                if status == "Kontrol Ediliyor":
                    updates.append("check_count = check_count + 1")
                    
                args.append(user_id)
                cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(args))
        finally:
            db.release_connection(conn)
            
    @staticmethod
    def delete(user_id: int) -> bool:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
                return cur.rowcount > 0
        finally:
            db.release_connection(conn)

    @staticmethod
    def set_cooldown(user_id: int, cooldown_timestamp: str):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET cooldown_until = %s WHERE id = %s", (cooldown_timestamp, user_id))
        finally:
            db.release_connection(conn)
            
    @staticmethod
    def update_worker_state(user_id: int, state: str):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET worker_state = %s WHERE id = %s", (state, user_id))
        finally:
            db.release_connection(conn)


class ProxyRepository:
    @staticmethod
    def get_all() -> List[Dict]:
        conn = db.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM proxies")
                return [dict(row) for row in cur.fetchall()]
        finally:
            db.release_connection(conn)

    @staticmethod
    def create(address: str) -> bool:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO proxies (address) VALUES (%s) ON CONFLICT (address) DO NOTHING",
                    (address,)
                )
                return cur.rowcount > 0
        finally:
            db.release_connection(conn)

    @staticmethod
    def update_proxy_status(address: str, status: str, fail_increment: int = 0, success_increment: int = 0, consecutive_fails: int = None, disabled_until: str = None):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                if consecutive_fails is not None:
                    # strict overwrite
                    cur.execute("""
                        UPDATE proxies 
                        SET status = %s, fail_count = fail_count + %s, success_count = success_count + %s, 
                            consecutive_fails = %s, disabled_until = %s 
                        WHERE address = %s
                    """, (status, fail_increment, success_increment, consecutive_fails, disabled_until, address))
                else:
                    cur.execute("""
                        UPDATE proxies 
                        SET status = %s, fail_count = fail_count + %s, success_count = success_count + %s
                        WHERE address = %s
                    """, (status, fail_increment, success_increment, address))
        finally:
            db.release_connection(conn)
            
    @staticmethod
    def delete(address: str):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM proxies WHERE address=%s", (address,))
        finally:
            db.release_connection(conn)


class GlobalSettingsRepository:
    @staticmethod
    def get(key: str, default: str = "") -> str:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM global_settings WHERE key=%s", (key,))
                row = cur.fetchone()
                return str(row[0]) if row else str(default)
        finally:
            db.release_connection(conn)

    @staticmethod
    def get_all() -> Dict[str, str]:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM global_settings")
                return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            db.release_connection(conn)

    @staticmethod
    def set(key: str, value: Any):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO global_settings (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (key, str(value))
                )
        finally:
            db.release_connection(conn)
