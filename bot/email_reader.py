"""
IMAP E-Mail OTP Reader
BLS randevu onay kodlarını (OTP) e-mail'den otomatik okur.
Her kullanıcının kendi e-mail/şifre bilgisiyle IMAP bağlantısı kurar.
"""
import imaplib
import email
import re
import time
import logging
from email.header import decode_header

logger = logging.getLogger(__name__)

# ── IMAP sunucu otomatik algılama ──────────────────────────────────────────
IMAP_SERVERS = {
    "gmail.com":     "imap.gmail.com",
    "googlemail.com":"imap.gmail.com",
    "hotmail.com":   "outlook.office365.com",
    "outlook.com":   "outlook.office365.com",
    "live.com":      "outlook.office365.com",
    "msn.com":       "outlook.office365.com",
    "yahoo.com":     "imap.mail.yahoo.com",
    "yandex.com":    "imap.yandex.com",
    "yandex.ru":     "imap.yandex.com",
    "icloud.com":    "imap.mail.me.com",
    "me.com":        "imap.mail.me.com",
}

def _detect_imap_server(email_address: str) -> str:
    """E-mail domain'inden IMAP sunucusunu otomatik belirle."""
    domain = email_address.split("@")[-1].lower().strip()
    if domain in IMAP_SERVERS:
        return IMAP_SERVERS[domain]
    # Fallback: imap.domain.com
    return f"imap.{domain}"


class OTPReader:
    """
    IMAP ile BLS OTP e-mailini okur ve 6 haneli kodu döndürür.
    
    Kullanım:
        reader = OTPReader("user@gmail.com", "app_password_here")
        otp = reader.wait_for_otp(timeout=120)
        # otp = "986501"
    """
    
    def __init__(self, email_address: str, app_password: str, imap_server: str = None, log_func=None):
        self.email_address = email_address
        self.app_password = app_password
        self.imap_server = imap_server or _detect_imap_server(email_address)
        self._mail = None
        self._log = log_func or (lambda lvl, msg: logger.log(lvl, msg))
    
    def _connect(self):
        """IMAP sunucusuna bağlan."""
        try:
            self._mail = imaplib.IMAP4_SSL(self.imap_server, 993)
            self._mail.login(self.email_address, self.app_password)
            self._log(logging.INFO, f"IMAP bağlantısı başarılı: {self.email_address} → {self.imap_server}")
            return True
        except imaplib.IMAP4.error as e:
            self._log(logging.ERROR, f"🚨 IMAP giriş hatası (Şifre yanlış veya IMAP erişimi kapalı): {e}")
            return False
        except Exception as e:
            self._log(logging.ERROR, f"🚨 IMAP bağlantı hatası ({self.imap_server}): {e}")
            return False
    
    def _disconnect(self):
        """IMAP bağlantısını kapat."""
        if self._mail:
            try:
                self._mail.logout()
            except Exception:
                pass
            self._mail = None
    
    def wait_for_otp(self, timeout: int = 120, poll_interval: int = 5) -> str:
        """
        BLS OTP e-mailini bekle ve 6 haneli kodu döndür.
        """
        if not self._connect():
            return None
        
        start_time = time.time()
        self._log(logging.INFO, f"OTP bekleniyor... ({timeout}sn limit)")
        
        try:
            while time.time() - start_time < timeout:
                otp = self._check_for_otp()
                if otp:
                    self._log(logging.INFO, f"✅ HEDEF YAKALANDI: OTP bulundu -> {otp}")
                    return otp
                
                elapsed = int(time.time() - start_time)
                if elapsed % 15 == 0 and elapsed > 0:
                    self._log(logging.INFO, f"Gelen Kutusu (IMAP) taranıyor... ({elapsed}sn geçti)")
                
                time.sleep(poll_interval)
            
            self._log(logging.ERROR, f"❌ OTP süresi doldu ({timeout}sn).")
            return None
        finally:
            self._disconnect()
    
    def _check_for_otp(self) -> str:
        """INBOX'ta son BLS OTP mailini ara."""
        try:
            self._mail.select("INBOX")
            
            # Son 10 maili kontrol et (en yeniden en eskiye)
            # DİKKAT: UNSEEN kullanmıyoruz çünkü telefondan veya bilgisayardan
            # bildirim gelince kazara "okundu" işaretlenebilir ve bot maili atlar!
            search_criteria = [
                '(FROM "bls")',
                '(FROM "blsinternational")',
                '(SUBJECT "verification")',
                '(SUBJECT "OTP")',
            ]
            
            for criteria in search_criteria:
                try:
                    status, messages = self._mail.search(None, criteria)
                    if status != "OK":
                        continue
                    
                    msg_ids = messages[0].split()
                    if not msg_ids:
                        continue
                    
                    # En son 3 maili kontrol et (Bazen eski OTP'ler takılı kalabilir)
                    for latest_id in reversed(msg_ids[-3:]):
                        status, msg_data = self._mail.fetch(latest_id, "(RFC822)")
                        if status != "OK":
                            continue
                        
                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        
                        # E-mail body'sini al
                        body = self._get_email_body(msg)
                        if not body:
                            continue
                        
                        # OTP kodunu bul (6 haneli sayı)
                        otp = self._extract_otp(body)
                        if otp:
                            # Maili okundu olarak işaretle (Silmiyoruz, sadece işaretliyoruz)
                            self._mail.store(latest_id, '+FLAGS', '\\Seen')
                            return otp
                            
                except Exception as e:
                    logger.debug(f"IMAP arama hatası ({criteria}): {e}")
                    continue
            
            return None
            
        except Exception as e:
            logger.debug(f"IMAP kontrol hatası: {e}")
            # Bağlantı kopmuş olabilir, yeniden bağlan
            try:
                self._connect()
            except Exception:
                pass
            return None
    
    def _get_email_body(self, msg) -> str:
        """E-mail body'sini text olarak al."""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        body += part.get_payload(decode=True).decode(charset, errors='ignore')
                    except Exception:
                        pass
                elif content_type == "text/html":
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        html = part.get_payload(decode=True).decode(charset, errors='ignore')
                        # HTML'den basit text çıkar
                        import re as _re
                        body += _re.sub(r'<[^>]+>', ' ', html)
                    except Exception:
                        pass
        else:
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, errors='ignore')
                if msg.get_content_type() == "text/html":
                    import re as _re
                    body = _re.sub(r'<[^>]+>', ' ', body)
            except Exception:
                pass
        
        return body
    
    def _extract_otp(self, body: str) -> str:
        """E-mail body'sinden 6 haneli OTP kodunu çıkar."""
        if not body:
            return None
        
        # BLS Mail Formatı (Görsele Göre):
        # "Your verification code is as mentioned below \n 355916"
        patterns = [
            r'below\s*\n\s*(\d{6})',                             # 'below' kelimesinin altındaki 6 hane
            r'(?:OTP|code|verification|kod)\s*[:=]?\s*(\d{6})',  # Klasik OTP yanındaki rakam
            r'\b(\d{6})\b',                                      # GENEL: Metindeki HERHANGİ bir ayrık 6 haneli sayı (Son Çare)
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def test_connection(self) -> dict:
        """IMAP bağlantısını test et."""
        result = {
            "success": False,
            "server": self.imap_server,
            "email": self.email_address,
            "message": ""
        }
        
        try:
            if self._connect():
                self._mail.select("INBOX")
                status, messages = self._mail.search(None, "ALL")
                total = len(messages[0].split()) if status == "OK" else 0
                result["success"] = True
                result["message"] = f"Bağlantı başarılı. Inbox'ta {total} mail var."
            else:
                result["message"] = "IMAP giriş başarısız. Şifreyi kontrol edin."
        except Exception as e:
            result["message"] = f"Bağlantı hatası: {str(e)[:100]}"
        finally:
            self._disconnect()
        
        return result
