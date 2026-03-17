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
    from data.repositories import UserRepository, GlobalSettingsRepository
    
    _captcha_check_counter = 0
    _last_balance_alert_time = 0  # Son bakiye uyarısı zamanı (epoch)
    
    while True:
        try:
            # 1. Update Quarantined Accounts from DB
            users = UserRepository.get_all()
            cooldowns = sum(1 for u in users if u.get("status") == "Cooldown")
            active = sum(1 for u in users if u.get("status") not in ["Idle", "Durduruldu", "Hata", "Giriş Hatası", "Cooldown"])
            
            METRIC_QUARANTINED_ACCOUNTS.set(cooldowns)
            METRIC_ACTIVE_WORKERS.set(active)
            
            # 2. Update Healthy Proxies from Redis
            r = redis_manager.get_client()
            if redis_manager.is_connected and r:
                healthy_count = r.scard("Proxy:ActiveList")
                METRIC_HEALTHY_PROXIES.set(healthy_count)

            # 3. 2Captcha Balance Check (every ~5 minutes = 20 cycles × 15s)
            _captcha_check_counter += 1
            if _captcha_check_counter >= 20:
                _captcha_check_counter = 0
                try:
                    api_key = GlobalSettingsRepository.get("2captcha_key", "").strip()
                    if api_key:
                        import requests
                        resp = requests.get(
                            f"https://2captcha.com/res.php?key={api_key}&action=getbalance",
                            timeout=10
                        )
                        balance = float(resp.text)
                        if balance < 1.0:
                            logger.critical(f"⚠️ 2Captcha bakiye kritik düşük: ${balance:.2f}")
                            # Telegram alert — minimum 6 saatte bir gönder
                            if time.time() - _last_balance_alert_time >= 6 * 3600:
                                try:
                                    bot_token = GlobalSettingsRepository.get("telegram_bot_token", "").strip()
                                    admin_ids_raw = GlobalSettingsRepository.get("telegram_admin_id", "").strip()
                                    if bot_token and admin_ids_raw:
                                        import urllib.request, json as _json, ssl
                                        ctx = ssl._create_unverified_context()
                                        for aid in [x.strip() for x in admin_ids_raw.split(",") if x.strip()]:
                                            try:
                                                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                                                payload = _json.dumps({"chat_id": aid, "text": f"⚠️ 2Captcha bakiye kritik: ${balance:.2f}"}).encode()
                                                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                                                urllib.request.urlopen(req, timeout=10, context=ctx)
                                            except Exception:
                                                pass
                                    _last_balance_alert_time = time.time()
                                except Exception:
                                    pass
                        else:
                            logger.info(f"💰 2Captcha bakiye: ${balance:.2f}")
                except Exception as ce:
                    logger.debug(f"2Captcha bakiye kontrol hatası: {ce}")

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
