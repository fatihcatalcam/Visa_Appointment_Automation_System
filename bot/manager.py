import time
import random
import threading
import logging
import queue
import datetime
from bot.scraper import BLSScraper
from config.database import get_active_users, update_user_status
from bot.dispatcher import scout_dispatcher

logger = logging.getLogger(__name__)

class WorkerThread(threading.Thread):
    def __init__(self, user, global_config, log_queue, is_scout=False):
        super().__init__()
        self.user = user
        self.global_config = global_config
        self.log_queue = log_queue
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
        self.log_queue.put(record)
        # Terminale de bas
        logging.log(level, f"{prefix} {message}")
        
        # Dosyaya özel log yaz (Human readable)
        try:
            import os, time, json
            log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
            os.makedirs(log_dir, exist_ok=True)
            safe_name = "".join(x for x in self.user.get('first_name', 'user') if x.isalnum() or x.isspace()).replace(" ", "_")
            log_path = os.path.join(log_dir, f"{safe_name}_{self.user.get('id', '0')}.log")
            
            time_str = time.strftime("[%Y-%m-%d %H:%M:%S]")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time_str} {logging.getLevelName(level)} - {message}\n")
                
            # Structured JSON Logging (For monitoring tools)
            json_path = os.path.join(log_dir, "telemetry_metrics.json")
            with open(json_path, "a", encoding="utf-8") as jf:
                log_data = {
                    "timestamp": time.time(),
                    "level": logging.getLevelName(level),
                    "user_id": self.user.get('id', '0'),
                    "message": message,
                    "proxy": self.user.get('proxy_address', 'None')
                }
                jf.write(json.dumps(log_data) + "\n")
        except:
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
        from config.database import get_global_setting
        active_hours = get_global_setting("active_hours", "").strip()
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
        update_user_status(user_id, status="Başlatılıyor")

        try:
            # 0. Scout / Worker Sleep Logic
            # If scout mode is enabled globally, and this thread is NOT a scout, it should SLEEP
            scout_mode_global = int(self.global_config.get("scout_mode", 0)) == 1
            if scout_mode_global and not self.is_scout:
                 self._wait_for_scout(user_id)
                 if not self.running: return # Exited while sleeping
                 
            # Cooldown check
            cooldown_until = self.user.get('cooldown_until')
            if cooldown_until:
                import datetime
                try:
                    cd = datetime.datetime.strptime(cooldown_until, "%Y-%m-%d %H:%M:%S")
                    if datetime.datetime.now() < cd:
                        self._log(logging.WARNING, f"Hesap bekleme süresinde (Cooldown). {cd} tarihine kadar işlem yapılamaz.")
                        update_user_status(user_id, status="Cooldown", error_msg=f"Bekliyor: {cd}")
                        # Keep thread alive but sleeping deeply, or just exit. Exiting is better for resources.
                        return 
                except Exception:
                    pass

            from bot.proxy_manager import proxy_manager
            self.scraper = BLSScraper(user_data=self.user, global_config=self.global_config)
            
            # Update user proxy to the sticky one returned by scraper for UI if needed
            if hasattr(self.scraper, 'proxy') and self.scraper.proxy:
                 from config.database import add_or_update_user
                 self.user['proxy_address'] = self.scraper.proxy
                 add_or_update_user({"id": user_id, "proxy_address": self.scraper.proxy})
                 self._log(logging.INFO, f"Atanan Proxy: {self.scraper.proxy}")
            
            if not self.scraper.start_driver():
                self._log(logging.ERROR, "Chrome başlatılamadı.")
                update_user_status(user_id, status="Hata", error_msg="Chrome başlatılamadı.")
                return

            # Ana Döngü
            while self.running:
                # 1. Giriş Kontrolü
                if not self.scraper.is_logged_in:
                    # B2: Try cookie session first (skip login + CAPTCHA)
                    if self.scraper._load_cookies():
                        self._log(logging.INFO, "🍪 Cookie oturumu ile giriş yapıldı (Login atlandı).")
                        self.login_fail_count = 0
                        continue
                    
                    update_user_status(user_id, status="Giriş Yapılıyor")
                    email = self.user.get("email", "")
                    from config.database import _decrypt
                    pwd = _decrypt(self.user.get("password_enc", ""))
                    
                    success = self.scraper.login(email, pwd, solve_captcha=True)
                    if not success:
                        # Log the login failure. If it fails repeatedly, Cooldown logic kicks in
                        self.login_fail_count += 1
                        if self.login_fail_count >= 3:
                            self._log(logging.ERROR, "Üst üste 3 giriş hatası! Hesap 6 saat tatile alınıyor (Risk Engine).")
                            from config.database import set_user_cooldown
                            set_user_cooldown(user_id, hours=6)
                            return # Exit thread

                        self._log(logging.ERROR, "Giriş başarısız. 60 sn sonra tekrar denenecek.")
                        update_user_status(user_id, status="Giriş Hatası", error_msg="Giriş yapılamadı")
                        self._wait(60)
                        continue
                    else:
                        self.login_fail_count = 0 # Reset on success

                # 2. Randevu Kontrolü
                update_user_status(user_id, status="Kontrol Ediliyor")
                
                result = self.scraper.check_appointment_availability()
                
                now_str = time.strftime("%d.%m.%Y %H:%M:%S")

                if result.get("available"):
                    dates_str = ", ".join(result.get("dates", []))
                    self._log(logging.INFO, f"🎉 RANDEVU BULUNDU: {dates_str}")
                    update_user_status(user_id, status="RANDEVU BULUNDU", last_check=now_str)
                    
                    # Notify Dispatcher if Scout
                    if scout_mode_global and self.is_scout:
                        scout_dispatcher.report_date_found(result.get("raw_results", []))
                    
                    # Bildirimler (Discord/Telegram vs.)
                    self._send_notifications(dates_str)
                    
                    # B1: Oto-Randevu Alma
                    auto_book = int(self.user.get('auto_book', 0)) == 1
                    if auto_book:
                        self._log(logging.INFO, "📌 Otomatik randevu alma başlatılıyor...")
                        raw_results = result.get("raw_results", [])
                        target_slot = raw_results[0] if raw_results else None
                        book_result = self.scraper.book_appointment(target_slot=target_slot)
                        if book_result:
                            self._log(logging.INFO, "✅✅✅ RANDEVU BAŞARIYLA ALINDI! ✅✅✅")
                            update_user_status(user_id, status="RANDEVU ALINDI", last_check=now_str)
                        else:
                            self._log(logging.WARNING, "❌ Otomatik randevu alınamadı (slot kapanmış olabilir).")
                    else:
                        self._log(logging.INFO, "Oto-randevu kapalı. Manuel işlem bekleniyor.")
                    
                    # Bulduktan sonra bu botu bekletmeye al
                    self._log(logging.INFO, "Randevu bulunduğu için bot beklemeye alındı (1 Saat).")
                    self._wait(3600)
                else:
                    msg = result.get("message", "")
                    self._log(logging.INFO, f"Randevu yok. Neden: {msg}")
                    update_user_status(user_id, status="Bekliyor", error_msg=msg, last_check=now_str)
                    
                    if scout_mode_global and self.is_scout:
                        scout_dispatcher.report_no_date()
                        
                # 3. Bekleme Süresi -> If we are a normal worker and dates expired, go back to sleep
                if scout_mode_global and not self.is_scout and not scout_dispatcher.is_date_available:
                     self._log(logging.INFO, "Randevular tükendi. İşçi bot uyku moduna geçiyor...")
                     if self.scraper:
                         try:
                             self.scraper.stop_driver() # Kapat ki RAM yemesin
                         except: pass
                     self._wait_for_scout(user_id)
                     if not self.running: break
                     continue # Restart loop to init scraper again
                # Check interval süresini küçük parçalara bölerek bekle (B3: jitter otomatik)
                
                # B5: Aktif saat penceresi kontrolü
                if not self._is_within_active_hours():
                    self._log(logging.INFO, "🌙 Aktif saatler dışında. Pencere açılana kadar bekleniyor...")
                    update_user_status(user_id, status="Zaman Dışı")
                    while self.running and not self._is_within_active_hours():
                        time.sleep(30)
                    if not self.running: break
                    self._log(logging.INFO, "☀️ Aktif saat penceresi açıldı! Taramaya devam ediliyor.")
                # Adaptive load jitter: add 0-30s random delay to prevent thundering herd
                wait = check_interval + random.randint(0, 30)
                self._wait(wait)

        except Exception as e:
            self._log(logging.ERROR, f"Bot döngüsü çöktü: {e}")
            update_user_status(user_id, status="Hata", error_msg=str(e)[:50])
        finally:
            if self.scraper:
                try:
                    self.scraper.stop_driver()
                except:
                    pass
            update_user_status(user_id, status="Durduruldu")
            self._log(logging.INFO, "Bot durduruldu.")

    def _send_notifications(self, dates_str):
        """B4: Tüm bildirim kanallarına gönder (Discord + CallMeBot + Telegram Bot Proaktif)"""
        try:
            from bot.notifier import DiscordNotifier, CallMeBotNotifier
            discord_wh = self.global_config.get("discord_webhook", "")
            if discord_wh:
                msg = f"🎉 **{self.user.get('first_name')} İçin RANDEVU BULUNDU!**\n📅 Tarihler: {dates_str}"
                DiscordNotifier(discord_wh).send_message(msg)
                
            telegram_user = self.global_config.get("telegram_username", "")
            telegram_api = self.global_config.get("telegram_apikey", "")
            if telegram_user and telegram_api:
                 msg = f"RANDEVU BULUNDU {self.user.get('first_name')} - {dates_str}"
                 CallMeBotNotifier(telegram_user, telegram_api).send_message(msg)
            
            # B4: Proactive Telegram Bot push (admin'e direkt mesaj)
            try:
                from bot.telegram_controller import send_telegram_alert
                alert_msg = f"🎉 <b>RANDEVU BULUNDU!</b>\n👤 {self.user.get('first_name')} {self.user.get('last_name', '')}\n📅 {dates_str}"
                send_telegram_alert(alert_msg)
            except Exception:
                pass  # Telegram bot başlatılmamışsa sessizce geç
                
        except Exception as e:
            self._log(logging.ERROR, f"Bildirim hatası: {e}")

    def _wait_for_scout(self, user_id):
        """Worker thread sleeps here until ScoutDispatcher wakes it up"""
        self._log(logging.INFO, "Zzz... İşçi bot uyku modunda. İzcinin randevu bulmasını bekliyor.")
        update_user_status(user_id, status="Uyku (Scout Bekleniyor)")
        
        while self.running:
            # Wait for event with a timeout so we can still shut down gracefully
            woke_up = scout_dispatcher.wait_for_dates(timeout=2.0)
            if woke_up and scout_dispatcher.is_date_available:
                self._log(logging.INFO, "🚀 İşçi bot uyandırıldı! Randevu akını başlıyor...")
                break

    def stop(self):
        self._log(logging.INFO, "Durdurma sinyali alındı. (Mevcut işlem bitince kapanacak)")
        self.running = False


class BotManager:
    def __init__(self, log_queue):
        self.threads = {}
        self.log_queue = log_queue

    def start_all(self):
        users = get_active_users()
        from config.database import get_global_setting
        global_config = {
            "2captcha_key": get_global_setting("2captcha_key"),
            "discord_webhook": get_global_setting("discord_webhook"),
            "telegram_username": get_global_setting("telegram_username"),
            "telegram_apikey": get_global_setting("telegram_apikey"),
            "scout_mode": get_global_setting("scout_mode", "0")
        }

        def _staggered_boot():
            import time
            scout_mode = int(global_config.get("scout_mode", 0)) == 1
                    
            for user in users:
                uid = user["id"]
                if uid not in self.threads or not self.threads[uid].is_alive():
                    # Scout mode is ON globally AND user is explicitly marked as Scout
                    is_scout = scout_mode and (int(user.get("is_scout", 0)) == 1)
                    
                    t = WorkerThread(user, global_config, self.log_queue, is_scout=is_scout)
                    t.daemon = True
                    t.start()
                    self.threads[uid] = t
                    
                    if not scout_mode or is_scout:
                        time.sleep(10) # Stagger boot for scouts or normal mode
                    else:
                        time.sleep(1) # Fast boot for sleeping workers
                    
        # Ana UI'yi kitlememek için ayrı bir Thread oluşturup başlatma döngüsünü ona veriyoruz
        boot_thread = threading.Thread(target=_staggered_boot, daemon=True)
        boot_thread.start()

    def stop_all(self):
        for uid, t in self.threads.items():
            t.stop()
        self.threads.clear()
        self.log_queue.put(logging.LogRecord("", logging.INFO, "", 0, "[SİSTEM] Temizlik sinyali gönderildi. Tüm botlar (chrome.exe) kendi içinde güvenle kapatılıyor...", (), None))
        
    def stop_user(self, user_id):
        if user_id in self.threads:
             self.threads[user_id].stop()
             del self.threads[user_id]
             
    def start_single(self, user_id):
        from config.database import get_user_by_id, get_global_setting
        user = get_user_by_id(user_id)
        if not user or not user.get('is_active'):
            self.log_queue.put(logging.LogRecord("", logging.WARNING, "", 0, f"[SİSTEM] Müşteri ID {user_id} bulunamadı veya pasif durumda.", (), None))
            return
            
        global_config = {
            "2captcha_key": get_global_setting("2captcha_key"),
            "discord_webhook": get_global_setting("discord_webhook"),
            "telegram_username": get_global_setting("telegram_username"),
            "telegram_apikey": get_global_setting("telegram_apikey"),
            "scout_mode": get_global_setting("scout_mode")
        }
        
        if user_id not in self.threads or not self.threads[user_id].is_alive():
            scout_mode = int(global_config.get("scout_mode", 0)) == 1
            is_scout = scout_mode and (int(user.get("is_scout", 0)) == 1)
            
            t = WorkerThread(user, global_config, self.log_queue, is_scout=is_scout)
            t.daemon = True
            t.start()
            self.threads[user_id] = t
            self.log_queue.put(logging.LogRecord("", logging.INFO, "", 0, f"[SİSTEM] Müşteri ID {user_id} (Manuel/Harici) başlatıldı.", (), None))
