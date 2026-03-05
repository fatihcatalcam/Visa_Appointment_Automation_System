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

class PostgresDatabase:
    """Strict PostgreSQL connection pool manager. Crashing SQLite fallback has been removed."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PostgresDatabase, cls).__new__(cls)
            cls._instance._init_pg()
        return cls._instance

    def _init_pg(self):
        self.is_connected = False
        self.pool = None
        self.dsn = os.getenv("PG_DSN", DEFAULT_DSN)
        
        try:
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=self.dsn
            )
            self.is_connected = True
            logger.info("🐘 PostgreSQL DAL bağlantı havuzu başarılı (2-20 bağlantı).")
        except Exception as e:
            self.is_connected = False
            logger.error(f"❌ PostgreSQL'e bağlanılamadı. Sistemin çalışması için PostgreSQL şarttır! Hata: {e}")
            raise Exception("PostgreSQL bağlantısı kurulamadı. Veritabanı hizmetinizi kontrol edin.")

    def get_connection(self):
        """Havuzdan bir bağlantı al. Caller MUTLAKA release_connection() çağırmalı."""
        if not self.is_connected or not self.pool:
            raise Exception("PostgreSQL bağlantı havuzu aktif değil.")
            
        try:
            conn = self.pool.getconn()
            conn.autocommit = True
            return conn
        except Exception as e:
            logger.error(f"DB Connection Error: {e}")
            raise

    def release_connection(self, conn):
        """Kullanılan bağlantıyı havuza geri ver."""
        if conn and self.pool:
            try:
                self.pool.putconn(conn)
            except Exception:
                pass

# Global Instance
db = PostgresDatabase()
