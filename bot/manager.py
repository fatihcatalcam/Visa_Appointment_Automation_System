import time
import os
import json
import random
import threading
import logging
import queue
import collections
import datetime
from bot.scraper import BLSScraper
from data.repositories import UserRepository, GlobalSettingsRepository
from bot.dispatcher import scout_dispatcher

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# P1: Thread-safe file log writing + JSON log rotation
# ════════════════════════════════════════════════════════════════════════════

_file_log_lock = threading.Lock()

MAX_LOG_FILE_SIZE = 50 * 1024 * 1024   # 50 MB
MAX_ROTATED_FILES = 3                   # Keep 3 rotated copies


def _rotate_log_if_needed(json_path):
    """Rotate telemetry_metrics.json when it exceeds MAX_LOG_FILE_SIZE."""
    try:
        if not os.path.exists(json_path):
            return
        if os.path.getsize(json_path) < MAX_LOG_FILE_SIZE:
            return
        # Rotate: .json → .json.1 → .json.2 → .json.3 (oldest deleted)
        for i in range(MAX_ROTATED_FILES, 0, -1):
            src = f"{json_path}.{i}"
            dst = f"{json_path}.{i + 1}"
            if os.path.exists(src):
                if i >= MAX_ROTATED_FILES:
                    os.remove(src)
                else:
                    os.rename(src, dst)
        os.rename(json_path, f"{json_path}.1")
        logger.info(f"📁 Log dosyası döndürüldü: {json_path}")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# P3: LogFanOut — non-destructive ring buffer for multiple consumers
# ════════════════════════════════════════════════════════════════════════════

class LogFanOut:
    """Thread-safe ring buffer with multiple independent readers (non-destructive)."""

    def __init__(self, maxlen=5000):
        self._buffer = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def push(self, record):
        with self._lock:
            self._buffer.append((self._seq, record))
            self._seq += 1

    def read_since(self, last_seq, limit=50):
        """Returns (new_seq, [records]) — non-destructive read."""
        with self._lock:
            results = [(s, r) for s, r in self._buffer if s > last_seq]
        entries = results[:limit]
        new_seq = entries[-1][0] if entries else last_seq
        return new_seq, [r for _, r in entries]

    @property
    def latest_seq(self):
        with self._lock:
            return self._seq


# ════════════════════════════════════════════════════════════════════════════
# WorkerThread
# ════════════════════════════════════════════════════════════════════════════

class WorkerThread(threading.Thread):
    def __init__(self, user, global_config, log_queue, log_fan_out, semaphore, is_scout=False):
        super().__init__()
        self.user = user
        self.global_config = global_config
        self.log_queue = log_queue
        self.log_fan_out = log_fan_out
        self._semaphore = semaphore
        self.running = True
        self.scraper = None
        self.login_fail_count = 0
        self.is_scout = is_scout

    def _log(self, level, message):
        """Kuyruğa log at (Arayüzde prefix ile göstermek için) ve dosyaya yaz"""
        prefix = f"[{self.user.get('first_name', 'Bilinmiyor')}]"
        record = logging.LogRecord(
            name=__name__, level=level, pathname="", lineno=0,
            msg=f"{prefix} {message}", args=(), exc_info=None
        )

        # P1: Bounded queue — drop oldest if full (ring buffer behavior)
        try:
            self.log_queue.put_nowait(record)
        except queue.Full:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.log_queue.put_nowait(record)
            except queue.Full:
                pass

        # P3: Non-destructive fan-out for WebSocket consumers
        self.log_fan_out.push(record)

        # Terminale de bas
        logging.log(level, f"{prefix} {message}")
        
        # P1: Thread-safe file writing + JSON log rotation
        try:
            log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
            os.makedirs(log_dir, exist_ok=True)
            safe_name = "".join(x for x in self.user.get('first_name', 'user') if x.isalnum() or x.isspace()).replace(" ", "_")
            log_path = os.path.join(log_dir, f"{safe_name}_{self.user.get('id', '0')}.log")
            json_path = os.path.join(log_dir, "telemetry_metrics.json")

            time_str = time.strftime("[%Y-%m-%d %H:%M:%S]")
            log_data = {
                "timestamp": time.time(),
                "level": logging.getLevelName(level),
                "user_id": self.user.get('id', '0'),
                "message": message,
                "proxy": self.user.get('proxy_address', 'None')
            }

            with _file_log_lock:
                _rotate_log_if_needed(json_path)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"{time_str} {logging.getLevelName(level)} - {message}\n")
                with open(json_path, "a", encoding="utf-8") as jf:
                    jf.write(json.dumps(log_data) + "\n")
        except Exception:
            pass

    def _wait(self, seconds):
        """Durdurma sinyali gelirse anında çıkabilmek için blokları saniyeye böler. Jitter ekler."""
        # B3: Human-like jitter (±15 saniye rastgelelik)
        jitter = random.randint(-15, 15)
        actual = max(10, seconds + jitter)
        for _ in range(actual):
            if not self.running:
                break
            time.sleep(1)

    def _is_within_active_hours(self):
        """B5: Aktif saat penceresi kontrolü. Pencere dışındaysa uyutur."""
        active_hours = GlobalSettingsRepository.get("active_hours", "").strip()
        if not active_hours or '-' not in active_hours:
            return True  # Ayar yoksa 7/24 çalış
        try:
            start_str, end_str = active_hours.split('-')
            now = datetime.datetime.now().time()
            start_t = datetime.time(*[int(x) for x in start_str.strip().split(':')])
            end_t = datetime.time(*[int(x) for x in end_str.strip().split(':')])
            if start_t <= end_t:
                return start_t <= now <= end_t
            else:  # Gece yarısını geçen pencereler: "22:00-06:00"
                return now >= start_t or now <= end_t
        except Exception:
            return True

    def run(self):
        user_id = self.user.get("id")
        first_name = self.user.get("first_name", "Bilinmiyor")
        check_interval = int(self.user.get("check_interval", 60))

        self._log(logging.INFO, f"Bot başlatılıyor... [Scout Modu: {'Aktif' if self.is_scout else 'Kapalı'}]")
        UserRepository.update_status(user_id, status="Başlatılıyor")

        # P0: Acquire semaphore slot before launching Chrome
        acquired = self._semaphore.acquire(timeout=600)  # Wait max 10 min for a slot
        if not acquired:
            self._log(logging.WARNING, "⏰ 10 dakika slot bekledikten sonra iptal edildi (max_workers aşıldı).")
            UserRepository.update_status(user_id, status="Kuyrukta (Limit)", error_msg="Worker limiti doldu")
            return

        try:
            # 0. Scout / Worker Sleep Logic
            # If scout mode is enabled globally, and this thread is NOT a scout, it should SLEEP
            scout_mode_global = int(self.global_config.get("scout_mode", 0)) == 1
            if scout_mode_global and not self.is_scout:
                 jurisdiction = self.user.get("jurisdiction", "").strip()
                 self._wait_for_scout(user_id, jurisdiction)
                 if not self.running: return # Exited while sleeping
                 
            # Cooldown check
            cooldown_until = self.user.get('cooldown_until')
            if cooldown_until:
                try:
                    cd = datetime.datetime.strptime(cooldown_until, "%Y-%m-%d %H:%M:%S")
                    if datetime.datetime.now() < cd:
                        self._log(logging.WARNING, f"Hesap bekleme süresinde (Cooldown). {cd} tarihine kadar işlem yapılamaz.")
                        UserRepository.update_status(user_id, status="Cooldown", error_msg=f"Bekliyor: {cd}")
                        return 
                except Exception:
                    pass

            from bot.proxy_manager import proxy_manager
            self.scraper = BLSScraper(user_data=self.user, global_config=self.global_config, log_func=self._log)
            
            # Update user proxy to the sticky one returned by scraper for UI if needed
            if hasattr(self.scraper, 'proxy') and self.scraper.proxy:
                 from config.database import add_or_update_user
                 self.user['proxy_address'] = self.scraper.proxy
                 add_or_update_user({"id": user_id, "proxy_address": self.scraper.proxy})
                 self._log(logging.INFO, f"Atanan Proxy: {self.scraper.proxy}")
            
            if not self.scraper.start_driver():
                self._log(logging.ERROR, "Chrome başlatılamadı.")
                UserRepository.update_status(user_id, status="Hata", error_msg="Chrome başlatılamadı.")
                return

            # Ana Döngü
            start_time = time.time()
            loop_error_count = 0  # Retry counter for error classifier
            while self.running:
              try:
                # B6: Soft-Restart (12 hours) to prevent memory fragmentation
                if time.time() - start_time > 12 * 3600:
                    self._log(logging.INFO, "♻️ 12 saatlik kesintisiz çalışma sınırı. Bellek şişmesini önlemek için tarayıcı yeniden başlatılıyor...")
                    if self.scraper:
                        try: self.scraper.stop_driver()
                        except Exception: pass
                    if not self.scraper.start_driver():
                        self._log(logging.ERROR, "Soft-restart başarısız oldu. İşçi thread kapatılıyor.")
                        return
                    start_time = time.time()
                    continue

                # 1. Giriş Kontrolü
                if not self.scraper.is_logged_in:
                    # B2: Try cookie session first (skip login + CAPTCHA)
                    if self.scraper._load_cookies():
                        self._log(logging.INFO, "🍪 Cookie oturumu ile giriş yapıldı (Login atlandı).")
                        self.login_fail_count = 0
                        continue
                    
                    UserRepository.update_status(user_id, status="Giriş Yapılıyor")
                    email = self.user.get("email", "")
                    from config.database import _decrypt
                    pwd = _decrypt(self.user.get("password_enc", ""))
                    
                    success = self.scraper.login(email, pwd, solve_captcha=True)
                    if not success:
                        # Log the login failure. If it fails repeatedly, Cooldown logic kicks in
                        self.login_fail_count += 1
                        if self.login_fail_count >= 3:
                            self._log(logging.ERROR, "Üst üste 3 giriş hatası! Hesap 6 saat tatile alınıyor (Risk Engine).")
                            dt = (datetime.datetime.now() + datetime.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
                            UserRepository.set_cooldown(user_id, dt)
                            return # Exit thread

                        self._log(logging.ERROR, "Giriş başarısız. 60 sn sonra tekrar denenecek.")
                        UserRepository.update_status(user_id, status="Giriş Hatası", error_msg="Giriş yapılamadı")
                        self._wait(60)
                        continue
                    else:
                        self.login_fail_count = 0 # Reset on success

                # 2. Randevu Kontrolü
                UserRepository.update_status(user_id, status="Kontrol Ediliyor")
                
                result = self.scraper.check_appointment_availability()
                
                now_str = time.strftime("%d.%m.%Y %H:%M:%S")

                # Reset error counter on successful check cycle
                loop_error_count = 0

                if result.get("available"):
                    dates_str = ", ".join(result.get("dates", []))
                    self._log(logging.INFO, f"🎉 RANDEVU BULUNDU: {dates_str}")
                    UserRepository.update_status(user_id, status="RANDEVU BULUNDU", last_check=now_str)
                    
                    # Notify Dispatcher if Scout
                    if scout_mode_global and self.is_scout:
                        jurisdiction = self.user.get("jurisdiction", "").strip()
                        scout_dispatcher.report_date_found(result.get("raw_results", []), location=jurisdiction)
                    
                    # Bildirimler (Discord/Telegram vs.)
                    self._send_notifications(dates_str)
                    
                    # B1: Oto-Randevu Alma
                    book_result = False
                    auto_book = int(self.user.get('auto_book', 0)) == 1
                    if auto_book:
                        self._log(logging.INFO, "📌 Otomatik randevu alma başlatılıyor...")
                        raw_results = result.get("raw_results", [])
                        target_slot = raw_results[0] if raw_results else None
                        book_result = self.scraper.book_appointment(target_slot=target_slot)
                        if book_result:
                            self._log(logging.INFO, "✅✅✅ RANDEVU BAŞARIYLA ALINDI! ✅✅✅")
                            UserRepository.update_status(user_id, status="RANDEVU ALINDI", last_check=now_str)
                        else:
                            self._log(logging.WARNING, "❌ Otomatik randevu alınamadı (slot kapanmış olabilir).")
                    else:
                        self._log(logging.INFO, "Oto-randevu kapalı. Manuel işlem bekleniyor.")
                    
                    # Randevu başarıyla alındıysa botu durdur
                    if book_result:
                        self._log(logging.INFO, "🛑 Randevu başarıyla alındığı için bot durduruluyor.")
                        self.running = False
                        return
                    else:
                        self._log(logging.INFO, "Randevu bulunamadı ama slot vardı, tekrar denenecek.")
                        self._wait(30)
                else:
                    msg = result.get("message", "")
                    self._log(logging.INFO, f"Randevu yok. Neden: {msg}")
                    UserRepository.update_status(user_id, status="Bekliyor", error_msg=msg, last_check=now_str)
                    
                    if scout_mode_global and self.is_scout:
                        jurisdiction = self.user.get("jurisdiction", "").strip()
                        scout_dispatcher.report_no_date(location=jurisdiction)
                        
                # 3. Bekleme Süresi -> If we are a normal worker and dates expired, go back to sleep
                if scout_mode_global and not self.is_scout:
                     jurisdiction = self.user.get("jurisdiction", "").strip()
                     is_avail = False
                     with scout_dispatcher.scout_lock:
                         state = scout_dispatcher.location_state.get(jurisdiction.lower(), {})
                         is_avail = state.get("is_available", False)
                         
                     if not is_avail:
                         self._log(logging.INFO, f"Randevular tükendi ({jurisdiction}). İşçi bot uyku moduna geçiyor...")
                         if self.scraper:
                             try:
                                 self.scraper.stop_driver() # Kapat ki RAM yemesin
                             except Exception: pass
                         self._wait_for_scout(user_id, jurisdiction)
                         if not self.running: break
                         continue # Restart loop to init scraper again
                # Check interval süresini küçük parçalara bölerek bekle (B3: jitter otomatik)
                
                # B5: Aktif saat penceresi kontrolü
                if not self._is_within_active_hours():
                    self._log(logging.INFO, "🌙 Aktif saatler dışında. Pencere açılana kadar bekleniyor...")
                    UserRepository.update_status(user_id, status="Zaman Dışı")
                    while self.running and not self._is_within_active_hours():
                        time.sleep(30)
                    if not self.running: break
                    self._log(logging.INFO, "☀️ Aktif saat penceresi açıldı! Taramaya devam ediliyor.")
                # Adaptive load jitter: add 0-30s random delay to prevent thundering herd
                wait = check_interval + random.randint(0, 30)
                self._wait(wait)

              except Exception as e:
                from bot.error_classifier import classify_error, get_weight, get_backoff
                error_type = classify_error(exception=e)
                weight = get_weight(error_type)
                proxy_info = f" [Proxy: {self.scraper.proxy}]" if self.scraper and self.scraper.proxy else ""
                self._log(logging.ERROR, f"Bot döngüsü hatası [{error_type}]{proxy_info}: {e}")
                UserRepository.update_status(user_id, status="Hata", error_msg=f"[{error_type}] {str(e)[:40]}")
                # Apply classified risk penalty
                if weight.account_risk_points > 0:
                    from config.database import report_account_risk
                    report_account_risk(user_id, weight.account_risk_points, reason=f"Worker crash: {error_type}")
                if weight.proxy_fails > 0 and self.scraper and self.scraper.proxy:
                    proxy_manager.report_failure(self.scraper.proxy, error_type=weight.proxy_error_type)
                # Retry with classified backoff instead of dying
                loop_error_count += 1
                if loop_error_count >= weight.max_retries:
                    self._log(logging.ERROR, f"Maksimum yeniden deneme ({weight.max_retries}) aşıldı. İşçi durduruluyor.")
                    break
                backoff_delay = get_backoff(error_type, loop_error_count - 1)
                self._log(logging.WARNING, f"Yeniden deneme {loop_error_count}/{weight.max_retries} — {backoff_delay}s bekleniyor...")
                self._wait(backoff_delay)

        finally:
            # P0: Always release the semaphore slot when thread exits
            self._semaphore.release()
            if self.scraper:
                try:
                    self.scraper.stop_driver()
                except Exception:
                    pass
            UserRepository.update_status(user_id, status="Durduruldu")
            self._log(logging.INFO, "Bot durduruldu.")

    def _send_notifications(self, dates_str):
        """B4: Tüm bildirim kanallarına gönder (Discord + Telegram Bot HTTP API)"""
        try:
            # 1. Discord Webhook
            discord_wh = self.global_config.get("discord_webhook", "")
            if discord_wh:
                try:
                    from bot.notifier import DiscordNotifier
                    msg = f"🎉 **{self.user.get('first_name')} İçin RANDEVU BULUNDU!**\n📅 Tarihler: {dates_str}"
                    DiscordNotifier(discord_wh).send_message(msg)
                    self._log(logging.INFO, "📨 Discord bildirimi gönderildi")
                except Exception as e:
                    self._log(logging.ERROR, f"Discord bildirim hatası: {e}")
            
            # 2. Telegram Bot — doğrudan HTTP API (daemon bağımsız)
            bot_token = self.global_config.get("telegram_bot_token", "").strip()
            admin_ids_raw = self.global_config.get("telegram_admin_id", "").strip()
            if bot_token and admin_ids_raw:
                try:
                    import urllib.request, json as _json, ssl
                    ctx = ssl._create_unverified_context()
                    admin_ids = [x.strip() for x in admin_ids_raw.split(",") if x.strip()]
                    alert_msg = f"🎉 RANDEVU BULUNDU!\n👤 {self.user.get('first_name')} {self.user.get('last_name', '')}\n📅 {dates_str}"
                    for aid in admin_ids:
                        try:
                            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                            payload = _json.dumps({"chat_id": aid, "text": alert_msg}).encode()
                            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                            urllib.request.urlopen(req, timeout=10, context=ctx)
                        except Exception as te:
                            self._log(logging.ERROR, f"Telegram bildirim hatası (ID {aid}): {te}")
                    self._log(logging.INFO, "📨 Telegram bildirimi gönderildi")
                except Exception as e:
                    self._log(logging.ERROR, f"Telegram bildirim hatası: {e}")
            
            # 3. Daemon-based fallback (eski yöntem — çalışıyorsa bonus)
            try:
                from bot.telegram_controller import send_telegram_alert
                alert_msg = f"🎉 <b>RANDEVU BULUNDU!</b>\n👤 {self.user.get('first_name')} {self.user.get('last_name', '')}\n📅 {dates_str}"
                send_telegram_alert(alert_msg)
            except Exception:
                pass
                
        except Exception as e:
            self._log(logging.ERROR, f"Bildirim hatası: {e}")

    def _wait_for_scout(self, user_id, jurisdiction):
        """Worker thread sleeps here until ScoutDispatcher wakes it up"""
        self._log(logging.INFO, f"Zzz... İşçi bot uyku modunda. İzcinin {jurisdiction} için randevu bulmasını bekliyor.")
        UserRepository.update_status(user_id, status="Uyku (Scout Bekleniyor)")
        
        while self.running:
            # Wait for event with a timeout so we can still shut down gracefully
            woke_up = scout_dispatcher.wait_for_dates(location=jurisdiction, timeout=2.0)
            
            is_avail = False
            with scout_dispatcher.scout_lock:
                state = scout_dispatcher.location_state.get(jurisdiction.lower(), {})
                is_avail = state.get("is_available", False)
                
            if woke_up and is_avail:
                self._log(logging.INFO, f"🚀 İşçi bot uyandırıldı! {jurisdiction} aktifleştirildi...")
                break

    def stop(self):
        self._log(logging.INFO, "Durdurma sinyali alındı. Chrome kapatılıyor...")
        self.running = False
        # Force-kill Chrome immediately — don't wait for the main loop to exit
        if self.scraper:
            try:
                self.scraper.stop_driver()
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════════════════
# BotManager — P0: Thread-safe with lock + semaphore cap
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_MAX_WORKERS = 15

class BotManager:
    def __init__(self, log_queue):
        self.threads = {}
        self.log_queue = log_queue
        self.log_fan_out = LogFanOut(maxlen=5000)
        self._lock = threading.Lock()   # P0: Guards self.threads dict

        # P0: Semaphore-based worker cap
        max_w = int(GlobalSettingsRepository.get("max_workers", str(DEFAULT_MAX_WORKERS)))
        self._semaphore = threading.Semaphore(max_w)
        self._max_workers = max_w

        # NOTE: We intentionally do NOT attach a handler to the root logger.
        # WorkerThread._log() and BotManager._sys_log() push directly to log_fan_out,
        # so only bot-relevant logs reach the Web UI. Adding a root handler would cause
        # duplication AND flood the panel with uvicorn/access/system noise.

        logger.info(f"🔧 BotManager initialized — max_workers={max_w}")
        
        # Start Profile Cleaner in background
        threading.Thread(target=self._profile_cleaner_loop, daemon=True).start()

    def _profile_cleaner_loop(self):
        """Runs once a day to clear bloated Chrome profile caches older than 7 days."""
        import shutil
        while True:
            try:
                profiles_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "profiles")
                if os.path.exists(profiles_dir):
                    now = time.time()
                    cleaned = 0
                    for item in os.listdir(profiles_dir):
                        item_path = os.path.join(profiles_dir, item)
                        if os.path.isdir(item_path):
                            # Check age of profile directory
                            try:
                                age_days = (now - os.path.getmtime(item_path)) / (3600 * 24)
                                if age_days > 7:
                                    shutil.rmtree(item_path, ignore_errors=True)
                                    cleaned += 1
                            except Exception:
                                pass
                    if cleaned > 0:
                        self._sys_log(logging.INFO, f"🧹 {cleaned} adet eski Chrome profili temizlendi (Disk tasarrufu).")
            except Exception as e:
                self._sys_log(logging.ERROR, f"Profil temizlik hatası: {e}")
            
            # Wipe logs older than 7 days too
            try:
                logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
                if os.path.exists(logs_dir):
                    now = time.time()
                    for item in os.listdir(logs_dir):
                        if item.endswith(".log"):
                            pth = os.path.join(logs_dir, item)
                            if (now - os.path.getmtime(pth)) / 86400 > 7:
                                os.remove(pth)
            except Exception: pass
            
            time.sleep(86400) # Check once every 24 hours

    def _sys_log(self, level, message):
        """System-level log entry (not tied to any worker)."""
        record = logging.LogRecord("", level, "", 0, f"[SİSTEM] {message}", (), None)
        try:
            self.log_queue.put_nowait(record)
        except queue.Full:
            pass
        self.log_fan_out.push(record)

    @property
    def active_worker_count(self):
        with self._lock:
            return sum(1 for t in self.threads.values() if t.is_alive())

    def start_all(self):
        users = UserRepository.get_active()
        global_config = {
            "2captcha_key": GlobalSettingsRepository.get("2captcha_key"),
            "discord_webhook": GlobalSettingsRepository.get("discord_webhook"),
            "telegram_username": GlobalSettingsRepository.get("telegram_username"),
            "telegram_apikey": GlobalSettingsRepository.get("telegram_apikey"),
            "telegram_bot_token": GlobalSettingsRepository.get("telegram_bot_token", ""),
            "telegram_admin_id": GlobalSettingsRepository.get("telegram_admin_id", ""),
            "scout_mode": GlobalSettingsRepository.get("scout_mode", "0")
        }

        def _staggered_boot():
            scout_mode = int(global_config.get("scout_mode", 0)) == 1
                    
            for user in users:
                uid = user["id"]
                with self._lock:
                    already_running = uid in self.threads and self.threads[uid].is_alive()
                if already_running:
                    continue

                is_scout = scout_mode and (int(user.get("is_scout", 0)) == 1)
                
                t = WorkerThread(user, global_config, self.log_queue, self.log_fan_out, self._semaphore, is_scout=is_scout)
                t.daemon = True
                t.start()
                with self._lock:
                    self.threads[uid] = t
                
                if not scout_mode or is_scout:
                    time.sleep(10) # Stagger boot for scouts or normal mode
                else:
                    time.sleep(1) # Fast boot for sleeping workers
                    
        # Ana UI'yi kitlememek için ayrı bir Thread oluşturup başlatma döngüsünü ona veriyoruz
        boot_thread = threading.Thread(target=_staggered_boot, daemon=True)
        boot_thread.start()

    def stop_all(self):
        with self._lock:
            threads_snapshot = dict(self.threads)
            self.threads.clear()
        for uid, t in threads_snapshot.items():
            t.stop()
        self._sys_log(logging.INFO, "Temizlik sinyali gönderildi. Tüm botlar (chrome.exe) kendi içinde güvenle kapatılıyor...")
        
    def stop_user(self, user_id):
        with self._lock:
            t = self.threads.pop(user_id, None)
        if t:
            t.stop()
             
    def start_single(self, user_id):
        user = UserRepository.get_by_id(user_id)
        if not user or not user.get('is_active'):
            self._sys_log(logging.WARNING, f"Müşteri ID {user_id} bulunamadı veya pasif durumda.")
            return
            
        global_config = {
            "2captcha_key": GlobalSettingsRepository.get("2captcha_key"),
            "discord_webhook": GlobalSettingsRepository.get("discord_webhook"),
            "telegram_username": GlobalSettingsRepository.get("telegram_username"),
            "telegram_apikey": GlobalSettingsRepository.get("telegram_apikey"),
            "telegram_bot_token": GlobalSettingsRepository.get("telegram_bot_token", ""),
            "telegram_admin_id": GlobalSettingsRepository.get("telegram_admin_id", ""),
            "scout_mode": GlobalSettingsRepository.get("scout_mode", "0")
        }
        
        with self._lock:
            already_running = user_id in self.threads and self.threads[user_id].is_alive()
        if already_running:
            self._sys_log(logging.WARNING, f"Müşteri ID {user_id} zaten çalışıyor.")
            return

        scout_mode = int(global_config.get("scout_mode", 0)) == 1
        is_scout = scout_mode and (int(user.get("is_scout", 0)) == 1)
        
        t = WorkerThread(user, global_config, self.log_queue, self.log_fan_out, self._semaphore, is_scout=is_scout)
        t.daemon = True
        t.start()
        with self._lock:
            self.threads[user_id] = t
        self._sys_log(logging.INFO, f"Müşteri ID {user_id} (Manuel/Harici) başlatıldı.")
