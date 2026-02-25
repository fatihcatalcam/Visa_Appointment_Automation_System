import os
import redis
import logging

logger = logging.getLogger(__name__)

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

class RedisManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisManager, cls).__new__(cls)
            cls._instance._init_redis()
        return cls._instance

    def _init_redis(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.client = None
        self.is_connected = False
        
        try:
            self.client = redis.Redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            self.is_connected = True
            logger.info("✅ Redis bağlantısı başarılı.")
        except redis.ConnectionError:
            self.is_connected = False
            logger.warning("⚠️ Redis'e bağlanılamadı! Docker container'ının (veya yerel Redis'in) çalıştığından emin olun.")
        except Exception as e:
            self.is_connected = False
            logger.error(f"Redis başlatılırken beklenmeyen hata: {e}")

    def get_client(self):
        """Returns the Redis client. Callers should check redis_manager.is_connected first."""
        return self.client

# Global singleton instance
redis_manager = RedisManager()
