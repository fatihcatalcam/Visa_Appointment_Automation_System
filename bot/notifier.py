import requests
import logging
import threading
import time
try:
    import winsound
except ImportError:
    winsound = None
# tkinter removed for headless VPS compatibility
try:
    from plyer import notification
except ImportError:
    notification = None

logger = logging.getLogger(__name__)

class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_message(self, message: str):
        if not self.webhook_url:
            return
        
        try:
            data = {"content": message}
            response = requests.post(self.webhook_url, json=data)
            if response.status_code == 204:
                logger.info("Discord bildirimi gönderildi.")
            else:
                logger.error(f"Discord bildirim hatası: {response.status_code}")
        except Exception as e:
            logger.error(f"Discord bildirim hatası: {e}")

class CallMeBotNotifier:
    def __init__(self, username: str, apikey: str):
        self.username = username.strip()
        if self.username.startswith('@'):
            self.username = self.username[1:]
        self.apikey = apikey.strip()
        
    def send_message(self, message: str):
        if not self.username or not self.apikey:
            return
            
        try:
            import urllib.parse
            encoded_message = urllib.parse.quote(message)
            url = f"https://api.callmebot.com/text.php?user=@{self.username}&text={encoded_message}&apikey={self.apikey}"
            
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram metin mesajı (CallMeBot) gönderildi.")
            else:
                logger.error(f"Telegram metin mesajı hatası (CallMeBot): HTTP {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Telegram metin mesajı hatası: {e}")

class Notifier:
    """Yerel Bildirimler (Ses, Windows Bildirimi)"""
    
    def __init__(self):
        self._alarm_active = False
        self._lock = threading.Lock()

    def notify_appointment_found(self, dates: list, sound=True, desktop=True):
        """Randevu bulunduğunda tetiklenir"""
        msg = f"Müsait Tarihler: {', '.join(dates)}"
        logger.info(f"YEREL BİLDİRİM: {msg}")
        
        if desktop and notification:
            try:
                notification.notify(
                    title="Randevu Bulundu!",
                    message=msg,
                    app_name="BLS Bot",
                    timeout=10
                )
            except: pass

        if sound and not self._alarm_active:
            self._start_alarm()

    def notify_booking_success(self):
        """Rezervasyon yapıldığında"""
        if notification:
            try:
                notification.notify(
                    title="Randevu Alındı!",
                    message="Otomatik rezervasyon başarılı!",
                    app_name="BLS Bot",
                    timeout=15
                )
            except: pass
        self._start_alarm()

    def _start_alarm(self):
        """Sürekli ses çal"""
        with self._lock:
            if self._alarm_active: return
            self._alarm_active = True
        
        threading.Thread(target=self._alarm_loop, daemon=True).start()

    def _alarm_loop(self):
        while self._alarm_active:
            try:
                if winsound:
                    winsound.Beep(1000, 500)  # 1000 Hz, 500 ms
                    time.sleep(0.5)
                    winsound.Beep(1500, 500)
                    time.sleep(0.5)
                else:
                    time.sleep(1)
            except:
                break

    def stop_alarm(self):
        with self._lock:
            self._alarm_active = False
