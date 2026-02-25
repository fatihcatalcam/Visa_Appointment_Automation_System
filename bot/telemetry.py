import threading
import logging
import time
from prometheus_client import start_http_server, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# --- Prometheus Metrics Definitions ---

# Counters (Monotonically increasing)
METRIC_LOGIN_ATTEMPTS = Counter('bot_login_attempts_total', 'Total login attempts', ['status'])
METRIC_CAPTCHAS_ENCOUNTERED = Counter('bot_captchas_total', 'Total captchas encountered')
METRIC_403_ERRORS = Counter('bot_403_errors_total', 'Total 403 Forbidden errors')
METRIC_BOOKING_SUCCESS = Counter('bot_booking_success_total', 'Total successful bookings')

# Gauges (Can go up and down)
METRIC_ACTIVE_WORKERS = Gauge('bot_active_workers', 'Number of currently active worker threads')
METRIC_HEALTHY_PROXIES = Gauge('bot_healthy_proxies', 'Number of Active proxies in the pool')
METRIC_QUARANTINED_ACCOUNTS = Gauge('bot_quarantined_accounts', 'Number of accounts currently in Cooldown/Quarantine')

# Histograms (For distributions like latency)
METRIC_PAGE_LOAD_LATENCY = Histogram('bot_page_load_latency_seconds', 'Page load latency in seconds')


def _poll_gauges_loop():
    """Periodically queries the true state of the system and updates Gauges."""
    from config.cache import redis_manager
    from config.database import get_all_users
    
    while True:
        try:
            # 1. Update Quarantined Accounts from DB
            users = get_all_users()
            cooldowns = sum(1 for u in users if u.get("status") == "Cooldown")
            active = sum(1 for u in users if u.get("status") not in ["Idle", "Durduruldu", "Hata", "Giriş Hatası", "Cooldown"])
            
            METRIC_QUARANTINED_ACCOUNTS.set(cooldowns)
            METRIC_ACTIVE_WORKERS.set(active)
            
            # 2. Update Healthy Proxies from Redis
            r = redis_manager.get_client()
            if redis_manager.is_connected and r:
                healthy_count = r.scard("Proxy:ActiveList")
                METRIC_HEALTHY_PROXIES.set(healthy_count)

        except Exception as e:
            logger.error(f"Error updating telemetry gauges: {e}")
        
        time.sleep(15)

def start_telemetry_server(port=8000):
    """
    Starts the Prometheus metrics HTTP server in a background daemon thread.
    This has zero blocking impact on the main GUI or the worker threads.
    """
    def run_server():
        try:
            start_http_server(port)
            logger.info(f"📊 Prometheus Telemetry Server started on port {port}")
            # Start the gauge poller
            threading.Thread(target=_poll_gauges_loop, daemon=True).start()
        except Exception as e:
            logger.error(f"Failed to start Prometheus server: {e}")

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
