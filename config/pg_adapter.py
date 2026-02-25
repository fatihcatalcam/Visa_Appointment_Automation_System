import os
import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

DEFAULT_DSN = "dbname='bot_db' user='bot_user' password='bot_password' host='localhost' port='5432'"

class PostgresManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PostgresManager, cls).__new__(cls)
            cls._instance._init_pg()
        return cls._instance

    def _init_pg(self):
        self.is_connected = False
        self.pool = None
        self.dsn = os.getenv("PG_DSN", DEFAULT_DSN)
        
        try:
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                dsn=self.dsn
            )
            self.is_connected = True
            logger.info("🐘 PostgreSQL bağlantı havuzu başarılı (2-10 bağlantı).")
            self._ensure_tables()
        except Exception as e:
            self.is_connected = False
            logger.warning(f"⚠️ PostgreSQL'e bağlanılamadı. SQLite Fallback devrede. Hata: {e}")

    def get_connection(self):
        """Havuzdan bir bağlantı al. Caller MUTLAKA release_connection() çağırmalı."""
        if not self.is_connected or not self.pool:
            return None
        try:
            conn = self.pool.getconn()
            conn.autocommit = True
            return conn
        except Exception:
            self.is_connected = False
            return None

    def release_connection(self, conn):
        """Kullanılan bağlantıyı havuza geri ver."""
        if conn and self.pool:
            try:
                self.pool.putconn(conn)
            except Exception:
                pass

    def _ensure_tables(self):
        """PostgreSQL şemasını oluştur veya güncelle"""
        if not self.is_connected: return
        conn = self.get_connection()
        if not conn: return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        is_active BOOLEAN DEFAULT TRUE,
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
                        headless BOOLEAN DEFAULT TRUE,
                        is_scout BOOLEAN DEFAULT FALSE,
                        auto_book BOOLEAN DEFAULT FALSE,
                        status TEXT DEFAULT 'Idle',
                        last_check TEXT,
                        check_count INTEGER DEFAULT 0,
                        error_msg TEXT,
                        cooldown_until TEXT,
                        worker_state TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS proxies (
                        id SERIAL PRIMARY KEY,
                        address TEXT UNIQUE NOT NULL,
                        status TEXT DEFAULT 'Active',
                        success_count INTEGER DEFAULT 0,
                        fail_count INTEGER DEFAULT 0,
                        consecutive_fails INTEGER DEFAULT 0,
                        last_used TEXT,
                        disabled_until TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS global_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                
                # Schema migration: add columns that may not exist yet
                migrations = [
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_scout BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_book BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS worker_state TEXT",
                ]
                for sql in migrations:
                    try:
                        cur.execute(sql)
                    except Exception:
                        pass
        finally:
            self.release_connection(conn)

# Global Instance
pg_manager = PostgresManager()
