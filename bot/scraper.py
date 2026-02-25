"""
BLS Spain Visa Bot - Scraper Module
Selenium tabanlı web scraping ve otomasyon motoru
"""

import os
import time
import logging
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from bot.captcha_solver import CaptchaSolver
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from bot.proxy_manager import proxy_manager
from config.database import report_account_risk, get_account_risk
from bot.telemetry import (
    METRIC_LOGIN_ATTEMPTS, METRIC_CAPTCHAS_ENCOUNTERED, 
    METRIC_403_ERRORS, METRIC_PAGE_LOAD_LATENCY, METRIC_BOOKING_SUCCESS
)
logger = logging.getLogger(__name__)


class BLSScraper:
    """BLS Spain Visa sitesi için Selenium scraper"""

    LOGIN_URL = "https://turkey.blsspainglobal.com/Global/account/login"
    REGISTER_URL = "https://turkey.blsspainglobal.com/Global/account/register"
    APPOINTMENT_URL = "https://turkey.blsspainglobal.com/Global/appointment/newappointment"

    def __init__(self, user_data: dict, global_config: dict = None):
        self.user_data = user_data
        self.config = global_config or {}
        self.headless = bool(self.user_data.get('headless', True))
        
        # Determine Proxy via ProxyManager
        # First check if the user has a hardcoded proxy, or if it was assigned dynamically
        assigned_proxy = self.user_data.get('proxy_address', "").strip()
        self.proxy = proxy_manager.get_proxy(assigned_proxy=assigned_proxy)
        
        # If proxy manager gave us a proxy, update the user data so it sticks (Sticky Session)
        if self.proxy:
            self.user_data['proxy_address'] = self.proxy
            
        self.driver = None
        self.wait = None
        self.is_logged_in = False
        
        # B2: Cookie session path
        self._session_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sessions')
        self._session_file = os.path.join(self._session_dir, f"{self.user_data.get('id', 0)}.json")

    def _generate_fingerprint(self):
        """Generates a consistent, semi-random fingerprint based on user_id"""
        import random
        # Seed the random generator with user_id so it always produces the same output for this user
        uid_str = str(self.user_data.get('id', '0'))
        random.seed(uid_str)
        
        # Pick a random but realistic Chrome version (e.g. 118-121)
        major_version = random.randint(118, 122)
        minor = random.randint(0, 9)
        build = random.randint(1000, 6000)
        patch = random.randint(0, 150)
        
        os_options = [
            "Windows NT 10.0; Win64; x64",
            "Windows NT 11.0; Win64; x64"
        ]
        os_ver = random.choice(os_options)
        
        ua = f"Mozilla/5.0 ({os_ver}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major_version}.0.{build}.{patch} Safari/537.36"
        
        # Window sizes
        resolutions = ["1280,900", "1366,768", "1440,900", "1600,900", "1920,1080"]
        res = random.choice(resolutions)
        
        return ua, res

    def start_driver(self):
        """Chrome WebDriver'ı başlat"""
        try:
            ua, res = self._generate_fingerprint()
            
            if self.headless:
                logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Gizli (Stealth Headless) modda başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
                import undetected_chromedriver as uc
                
                options = uc.ChromeOptions()
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument(f"--window-size={res}")
                if self.proxy:
                    if "@" in self.proxy:
                        from bot.proxy_auth import create_proxy_extension
                        ext_path = create_proxy_extension(self.proxy)
                        if ext_path:
                            # undetected-chromedriver extension paths must be comma separated
                            options.add_argument(f"--load-extension={ext_path.replace('.zip', '')}")
                            # Need to extract it for undetected_chromedriver or use add_extension if it works,
                            # actually uc works better with unpacked. Let's modify the auth generator slightly or just pass the zip.
                            # Standard Selenium uses add_extension for zip. UC prefers load-extension for unpacked.
                            # We will use the zip approach with standard options.
                            options.add_extension(ext_path)
                            logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Auth-Proxy Eklentisi Yüklendi.")
                    else:
                        options.add_argument(f"--proxy-server={self.proxy}")
                        logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Proxy Aktif: {self.proxy}")
                options.add_argument(f"user-agent={ua}")
                
                options.add_argument("--disable-gpu")
                options.add_argument("--enable-javascript")
                
                # Retry logic for known 'target window already closed' headless crash
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        self.driver = uc.Chrome(options=options, headless=True, use_subprocess=True)
                        break
                    except Exception as try_err:
                        if attempt == max_retries - 1:
                            raise try_err
                        logger.warning(f"[{self.user_data.get('first_name')}] Headless tarayıcı çöktü, tekrar deneniyor ({attempt+1}/{max_retries}): {try_err}")
                        time.sleep(2)

            else:
                logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Normal (Görünür) Chrome başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
                options = Options()
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-blink-features=AutomationControlled")
                if self.proxy:
                    if "@" in self.proxy:
                        from bot.proxy_auth import create_proxy_extension
                        ext_path = create_proxy_extension(self.proxy)
                        if ext_path:
                            options.add_extension(ext_path)
                            logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Auth-Proxy Eklentisi Yüklendi.")
                    else:
                        options.add_argument(f"--proxy-server={self.proxy}")
                        logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Proxy Aktif: {self.proxy}")
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option("useAutomationExtension", False)
                options.add_argument(f"--window-size={res}")
                options.add_argument(f"user-agent={ua}")
                
                # Selenium Manager (4.6+) doğru sürücüyü otomatik bulur
                self.driver = webdriver.Chrome(options=options)
            
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            # Add dynamic screen size overriding based on user logic to bypass basic detection
            width, height = res.split(',')
            self.driver.execute_script(f"""
                Object.defineProperty(window.screen, 'width', {{get: () => {width}}});
                Object.defineProperty(window.screen, 'height', {{get: () => {height}}});
            """)
            
            # Selenium Stealth Entegrasyonu
            try:
                from selenium_stealth import stealth
                vendor = random.choice(["Google Inc.", "Apple Computer, Inc."])
                renderer = random.choice(["Intel Iris OpenGL Engine", "AMD Radeon Pro 5300M OpenGL Engine", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"])
                
                stealth(self.driver,
                        languages=["tr-TR", "tr", "en-US", "en"],
                        vendor=vendor,
                        platform="Win32",
                        webgl_vendor="Intel Inc.",
                        renderer=renderer,
                        fix_hairline=True,
                        )
                logger.info(f"[{self.user_data.get('first_name', 'Bilinmiyor')}] Stealth JS Enjekte Edildi.")
            except ImportError:
                logger.warning("selenium-stealth kütüphanesi bulunamadı, standart ayarlar ile devam ediliyor.")
                
            self.wait = WebDriverWait(self.driver, 20)
            logger.info("Chrome WebDriver başlatıldı (Network Loglama Aktif)")
            return True
        except Exception as e:
            logger.error(f"WebDriver başlatma hatası: {e}")
            return False

    def dump_network_logs(self):
        """Performans loglarını JSON dosyasına kaydeder (API Analizi için)"""
        try:
            if not self.driver: return
            
            logs = self.driver.get_log("performance")
            import json
            
            # Sadece Network.requestWillBeSent veya responseReceived olaylarını filtrele
            # Dosya boyutunu küçültmek için
            filtered_logs = []
            for entry in logs:
                try:
                    msg = json.loads(entry["message"])["message"]
                    if "Network.requestWillBeSent" in msg["method"] or "Network.responseReceived" in msg["method"]:
                        # Sadece XHR/Fetch/Document isteklerini al (Resim/CSS gerek yok)
                        type_ = msg["params"].get("type", "") or msg["params"].get("request", {}).get("type", "") # Bazen type request içinde
                        #if type_ in ["XHR", "Fetch", "Document"]:
                        filtered_logs.append(msg) # Şimdilik hepsini alalım, endpoint'i kaçırmayalım
                except: pass
            
            with open("network_activity.json", "w", encoding="utf-8") as f:
                json.dump(filtered_logs, f, indent=2)
            
            logger.info(f"✅ Network logları kaydedildi: network_activity.json ({len(filtered_logs)} olay)")
        except Exception as e:
            logger.error(f"Log dump hatası: {e}")

    def stop_driver(self):
        """WebDriver'ı kapat — çıkmadan önce cookie'leri kaydet"""
        if self.driver:
            try:
                self._save_cookies()
            except Exception:
                pass
            try:
                self.driver.quit()
                proxy_manager.report_release(self.proxy)
            except Exception:
                pass
            finally:
                self.driver = None
            self.is_logged_in = False
            logger.info("WebDriver kapatıldı")

    def _save_cookies(self):
        """B2: Tarayıcı cookie'lerini diske kaydet"""
        if not self.driver:
            return
        try:
            import json
            os.makedirs(self._session_dir, exist_ok=True)
            cookies = self.driver.get_cookies()
            with open(self._session_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            logger.info(f"🍪 {len(cookies)} cookie kaydedildi: {self._session_file}")
        except Exception as e:
            logger.debug(f"Cookie kaydetme hatası: {e}")

    def _load_cookies(self):
        """B2: Kaydedilmiş cookie'leri yükle ve oturumun hâlâ geçerli olup olmadığını kontrol et"""
        if not self.driver or not os.path.exists(self._session_file):
            return False
        try:
            import json
            # Önce domain'e git ki cookie'ler set edilebilsin
            self.driver.get(self.LOGIN_URL)
            time.sleep(2)
            
            with open(self._session_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            
            for cookie in cookies:
                # Bazı Cookie alanları Selenium'da sorun çıkarabilir
                cookie.pop('sameSite', None)
                cookie.pop('storeId', None)
                try:
                    self.driver.add_cookie(cookie)
                except Exception:
                    pass
            
            logger.info(f"🍪 {len(cookies)} cookie yüklendi, oturum kontrol ediliyor...")
            
            # Appointment sayfasına giderek oturum geçerliliğini test et
            self.driver.get(self.APPOINTMENT_URL)
            time.sleep(3)
            
            current_url = self.driver.current_url.lower()
            if 'login' not in current_url and 'account' not in current_url:
                logger.info("✅ Cookie oturumu geçerli! Login atlanıyor.")
                self.is_logged_in = True
                return True
            else:
                logger.info("❌ Cookie oturumu süresi dolmuş. Normal login yapılacak.")
                return False
        except Exception as e:
            logger.debug(f"Cookie yükleme hatası: {e}")
            return False

    def login(self, email: str, password: str, solve_captcha: bool = True) -> bool:
        """BLS sitesine giriş yap — state-machine yaklaşımı"""
        try:
            logger.info("Giriş yapılıyor...")
            # Go to login page and measure latency
            start_time = time.time()
            self.driver.get(self.LOGIN_URL)
            end_time = time.time()
            
            # Report latency to the Proxy Manager and Prometheus
            latency_ms = (end_time - start_time) * 1000
            proxy_manager.report_latency(self.proxy, latency_ms)
            METRIC_PAGE_LOAD_LATENCY.observe(end_time - start_time)
            logger.info(f"Page Load Latency: {latency_ms:.0f}ms")

            time.sleep(2) # Anti-bot amaçlı bekleme (Site çok hızlı girişi şüpheli bulabilir)

            # ── Adım 1: Email alanını bul ve doldur ──────────────────────────
            visible_inputs = self._get_visible_text_inputs()
            logger.info(f"Görünür input sayısı: {len(visible_inputs)}")

            if not visible_inputs:
                logger.error("Hiç görünür input bulunamadı")
                return False

            email_field = visible_inputs[0]
            self.driver.execute_script(
                "arguments[0].value = arguments[1]; "
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                email_field, email
            )
            logger.info(f"Email girildi: {email}")
            time.sleep(0.5)

            # ── Adım 2: Verify butonu ─────────────────────────────────────────
            verify_btn = self._find_visible_button(["Verify", "verify", "VERIFY"])
            if verify_btn:
                logger.info("Verify tıklanıyor...")
                self.driver.execute_script("arguments[0].click();", verify_btn)
                time.sleep(3)

                # Erken başarı kontrolü — kullanıcı zaten giriş yaptı mı?
                if self._check_login_success():
                    return True

                # CAPTCHA var mı?
                if solve_captcha:
                    api_key = self.config.get("2captcha_key", "").strip()
                    captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                    if captcha_solver.is_captcha_present():
                        # KRİTİK KONTROL: Şifre alanı zaten var mı?
                        # Eğer şifre alanı varsa, önce şifre girilmeli. Captcha'yı burda çözme!
                        if self._find_password_field():
                            logger.info("Captcha ve Şifre alanı aynı anda tespit edildi. Önce şifre girilecek...")
                        else:
                            logger.info("Sadece CAPTCHA var (Email onayı olabilir). Çözülüyor...")
                            captcha_solver.solve()
                            # CAPTCHA sonrası giriş başarılı mı?
                            if self._check_login_success():
                                return True

                total_wait = 60 if not solve_captcha else 20
                logger.info(f"Şifre sayfası bekleniyor... ({total_wait/2} sn)")
                for _ in range(total_wait):
                    time.sleep(0.5)
                    if self._check_login_success():
                        return True
                    inputs = self._get_visible_text_inputs()
                    if inputs:
                        break
                    # Eğer Captcha varsa ve çözülmediyse hala bekliyor olabiliriz
                    if solve_captcha and captcha_solver.is_captcha_present() and not self._find_password_field():
                         logger.debug("Hala Captcha var ama şifre yok...")
                         
                    logger.debug("Şifre alanı henüz yok, bekleniyor...")

            # ── Adım 3: Şifre alanı ve CAPTCHA Döngüsü ───────────────────────
            # CAPTCHA yanlış girildiğinde sayfa yenilenebilir veya şifre silinebilir.
            # Bu yüzden şifre girme ve CAPTCHA çözme işlemini bir döngüde yapıyoruz.
            max_login_attempts = 3
            
            for attempt in range(max_login_attempts):
                logger.info(f"Giriş/Şifre Denemesi: {attempt + 1}/{max_login_attempts}")
                
                # Sayfanın yüklenmesi/yenilenmesi için kısa bir bekleme
                time.sleep(1)
                
                if self._check_login_success():
                    return True
                    
                password_field = self._find_password_field()

                if not password_field:
                    if attempt == 0:
                        # İlk denemede bulamazsa, belki kullanıcıya manuel giriş için süre tanınmalı
                        logger.warning("Şifre alanı bulunamadı — lütfen tarayıcıdan şifreyi girin (90 sn)")
                        for _ in range(90):
                            time.sleep(1)
                            if self._check_login_success():
                                logger.info("✅ Kullanıcı manuel giriş yaptı!")
                                self.is_logged_in = True
                                return True
                            password_field = self._find_password_field()
                            # Eğer şifre alanı geri geldiyse, döngüden çıkıp JS ile dolduracak
                            if password_field: 
                                break
                    
                    if not password_field:
                        logger.error("Şifre alanı bulunamadı, yeniden deneniyor...")
                        continue # Bir sonraki denemeye geç (belki sayfa yenileniyordur)

                # Şifre alanı varsa doldur
                if password_field:
                    try:
                        self.driver.execute_script(
                            "arguments[0].value = arguments[1]; "
                            "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                            password_field, password
                        )
                        logger.info("Şifre girildi")
                        time.sleep(0.5)
                    except Exception as pe:
                        logger.error(f"Şifre alanına yazılırken hata: {pe}")
                        continue # Hata olursa tekrar dene

                if not solve_captcha:
                    logger.warning("CAPTCHA ve Şifre (Varsa) Manuel Giriş için 20 sn bekleniyor...")
                    time.sleep(20)
                    if self._check_login_success(): return True
                    continue

                # ── CAPTCHA (Şifre girildikten sonra) ──
                api_key = self.config.get("2captcha_key", "").strip()
                captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                
                if captcha_solver.is_captcha_present():
                     logger.info("CAPTCHA çözülüyor (Şifre girildi, şimdi Submit edilecek)...")
                     if captcha_solver.solve():
                         logger.info("Captcha Submit edildi. Sonuç bekleniyor...")
                         time.sleep(5)
                         if self._check_login_success(log=True):
                             return True
                         else:
                             logger.warning("Captcha çözüldü ancak giriş başarılı olmadı. Şifre silinmiş olabilir, tekrar denenecek.")
                             # Bazen captcha submit sonrası başarısız olursa error message çıkar
                             self._check_login_success(log=False) # Hataları loglamak için
                     else:
                         logger.error("Captcha çözülemedi, tekrar denenecek.")
                else:
                    # Captcha yoksa ama şifre girildiyse, belki bir Login butonu vardır
                    login_btn = self._find_visible_button(["Login", "Giriş"])
                    if login_btn:
                        logger.info("CAPTCHA yok, Login butonuna tıklanıyor...")
                        self.driver.execute_script("arguments[0].click();", login_btn)
                        time.sleep(4)
                        if self._check_login_success(log=True):
                            return True
                    else:
                        logger.info("CAPTCHA yok, Login butonu da yok. Sayfanın yüklenmesi bekleniyor...")
                        time.sleep(2)
                        if self._check_login_success(log=True): return True

            # Döngü bitti ve girilemedi
            return self._check_login_success(log=True)

        except Exception as e:
            logger.error(f"Giriş hatası: {e}")
            return False

    def _check_login_success(self, log: bool = False) -> bool:
        """URL'ye bakarak giriş başarılı mı kontrol et"""
        try:
            url = self.driver.current_url.lower()
            if "login" not in url:
                self.is_logged_in = True
                if log:
                    logger.info(f"✅ Giriş başarılı! URL: {url}")
                METRIC_LOGIN_ATTEMPTS.labels('success').inc()
                return True
            if log:
                # Hata mesajlarını topla
                for sel in [".validation-summary-errors", ".field-validation-error",
                             ".alert-danger", ".error-message"]:
                    try:
                        errs = self.driver.find_elements(By.CSS_SELECTOR, sel)
                        for e in errs:
                            if e.text.strip():
                                logger.error(f"Hata: {e.text.strip()}")
                    except Exception:
                        pass
                logger.error(f"Giriş başarısız — URL: {url}")
                METRIC_LOGIN_ATTEMPTS.labels('fail').inc()
                proxy_manager.report_failure(self.proxy) # Report failure on login error
                report_account_risk(self.user_data.get('id'), 15, reason="Giriş Hatası (Login Fail)")
        except Exception:
            pass
        return False

    def _find_password_field(self):
        """Şifre alanını bul — type='password' veya görünür ikinci input"""
        # Önce type='password' dene
        try:
            pw_fields = self.driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            pw_fields = [f for f in pw_fields if f.is_displayed() and f.is_enabled()]
            if pw_fields:
                return pw_fields[0]
        except Exception:
            pass

        # Görünür input'ları al
        visible = self._get_visible_text_inputs()
        if len(visible) >= 2:
            return visible[1]  # İkinci input = şifre
        if len(visible) == 1:
            return visible[0]  # Tek input kaldıysa o şifre
        return None


    def _get_visible_text_inputs(self) -> list:
        """Sayfadaki görünür, doldurulabilir text/password input'larını döner"""
        try:
            inputs = self.driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='text'], input[type='password'], input[type='email'], input:not([type])"
            )
            result = []
            for inp in inputs:
                try:
                    if (inp.is_displayed() and inp.is_enabled()
                            and inp.get_attribute("type") not in ("hidden", "submit", "button",
                                                                   "checkbox", "radio", "file")):
                        result.append(inp)
                except Exception:
                    pass
            return result
        except Exception:
            return []

    def _find_visible_button(self, texts: list):
        """Verilen metinlerden birini içeren görünür butonu bul"""
        try:
            btns = self.driver.find_elements(By.TAG_NAME, "button")
            btns += self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            for btn in btns:
                if not btn.is_displayed():
                    continue
                btn_text = btn.text.strip() or btn.get_attribute("value") or ""
                for t in texts:
                    if t.lower() in btn_text.lower():
                        return btn
        except Exception:
            pass
        return None


    def _find_element_multi(self, selectors: list, timeout: int = 10):
        """Birden fazla seçici dener, ilk bulunanı döner"""
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            for by, value in selectors:
                try:
                    el = self.driver.find_element(by, value)
                    if el.is_displayed():
                        return el
                except Exception:
                    pass
            _time.sleep(0.5)
        return None

    def _solve_captcha_with_fallback(self, captcha_solver: "CaptchaSolver"):
        """CAPTCHA çöz, başarısız olursa kullanıcıya 60 sn ver"""
        captcha_ok = captcha_solver.solve()
        if not captcha_ok:
            logger.warning("CAPTCHA otomatik çözülemedi — lütfen tarayıcıdan manuel çözün (15 sn)")
            for _ in range(15):
                time.sleep(1)
                if not captcha_solver.is_captcha_present():
                    logger.info("CAPTCHA manuel olarak çözüldü")
                    break


    def register(self, email: str, password: str, first_name: str, last_name: str,
                 phone: str) -> bool:
        """Yeni hesap oluştur"""
        try:
            logger.info("Kayıt sayfasına gidiliyor...")
            self.driver.get(self.REGISTER_URL)
            time.sleep(2)

            # Form alanlarını doldur
            fields = {
                "FirstName": first_name,
                "LastName": last_name,
                "EmailId": email,
                "MobileNo": phone,
                "Password": password,
                "ConfirmPassword": password,
            }

            for field_id, value in fields.items():
                try:
                    field = self.wait.until(
                        EC.presence_of_element_located((By.ID, field_id))
                    )
                    field.clear()
                    field.send_keys(value)
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(f"Alan bulunamadı: {field_id} - {e}")

            # Kayıt butonu
            try:
                register_btn = self.driver.find_element(By.ID, "btnRegister")
                register_btn.click()
            except NoSuchElementException:
                # Alternatif buton arayışı
                btns = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    if "register" in btn.text.lower() or "kayıt" in btn.text.lower():
                        btn.click()
                        logger.info("Sisteme Giriş Yap butonu tıklandı.")
                        break

            # Risk-based Adaptive Delay
            # High risk accounts wait longer to simulate scared/careful human behavior
            risk_score = get_account_risk(self.user_data.get('id'))
            delay = 2
            if risk_score > 30: delay = 4
            if risk_score > 50: delay = 6
            time.sleep(delay)

            time.sleep(3)

            # Başarı kontrolü
            current_url = self.driver.current_url
            if "register" not in current_url.lower():
                logger.info("Kayıt başarılı!")
                return True
            else:
                try:
                    error = self.driver.find_element(By.CLASS_NAME, "validation-summary-errors")
                    logger.error(f"Kayıt hatası: {error.text}")
                except NoSuchElementException:
                    logger.warning("Kayıt durumu belirsiz")
                return False

        except Exception as e:
            logger.error(f"Kayıt hatası: {e}")
            return False

    def check_appointment_availability(self) -> dict:
        """
        Randevu müsaitliğini kontrol et.
        Returns: {
            'available': bool,
            'dates': list[str],
            'message': str
        }
        """
        result = {'available': False, 'dates': [], 'message': ''}

        try:
            if not self.is_logged_in:
                result['message'] = "Giriş yapılmamış"
                return result

            logger.info("Randevu sayfasına gidiliyor...")
            
            # ── HTTP Pre-Check: Site Ayakta Mı? ──
            import requests
            try:
                proxies = None
                if getattr(self, "proxy", None):
                    proxies = {"http": f"http://{self.proxy}", "https": f"http://{self.proxy}"}
                import urllib3
                urllib3.disable_warnings() # Gizler InsecureRequestWarning
                r = requests.head(self.APPOINTMENT_URL, timeout=10, proxies=proxies, verify=False)
                if r.status_code >= 500:
                    logger.warning(f"HTTP Pre-check başarısız (Status {r.status_code}) — site çökmüş olabilir.")
                    result['message'] = f"Site down (HTTP {r.status_code})"
                    return result
            except requests.exceptions.Timeout:
                logger.warning(f"HTTP Pre-check başarısız (Timeout). Site Offline.")
                result['message'] = "Site timeout (HTTP Pre-check)"
                return result
            except Exception as e:
                logger.warning(f"HTTP Pre-check Exception: {str(e)[:50]}")
                # Hata durumunda devam et, proxy kaynaklı basit bir ssl hatası olabilir.

            self.driver.get(self.APPOINTMENT_URL)
            time.sleep(3)
            self._check_and_solve_captcha()  # Sürpriz CAPTCHA kontrolü

            # Risk-based Delay for appointment page
            risk_score = get_account_risk(self.user_data.get('id'))
            time.sleep(3 if risk_score > 40 else 1)

            # ... Cloudflare veya "Access Denied" sayfa kontrolü eklenebilir
            if "Access Denied" in self.driver.page_source or "403 Forbidden" in self.driver.title:
                 logger.error("403 Forbidden Access!")
                 METRIC_403_ERRORS.inc()
                 report_account_risk(self.user_data.get('id'), 30, reason="403 Forbidden")
                 return {"available": False, "message": "403 BLOCKED", "dates": []}

            current_url = self.driver.current_url.lower()

            # Login'e yönlendirildik mi?
            if "login" in current_url:
                self.is_logged_in = False
                result['message'] = "Oturum sona erdi, yeniden giriş gerekiyor"
                return result

            # Sayfa içinde session expires mesajı var mı?
            try:
                page_text_lower = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                if "the user session is expired" in page_text_lower or "oturum sona erdi" in page_text_lower:
                    self.is_logged_in = False
                    result['message'] = "Oturum sona erdi, yeniden giriş gerekiyor"
                    return result
            except Exception:
                pass

            # ── Email onayı bekleniyor mu? ────────────────────────────────────
            if "dataprotection" in current_url or "emailsent" in current_url:
                result['message'] = (
                    "⚠️ Email onayı gerekiyor! "
                    "Kayıtlı email adresinize gelen linke tıklayın, "
                    "ardından bot otomatik devam edecek."
                )
                logger.warning("Email onayı bekleniyor — lütfen email'inizdeki linke tıklayın!")
                # Kullanıcı email'i onaylayana kadar bekle (max 5 dk)
                for _ in range(60):
                    time.sleep(5)
                    url = self.driver.current_url.lower()
                    if "dataprotection" not in url and "emailsent" not in url:
                        logger.info("Email onaylandı, devam ediliyor...")
                        # Tekrar randevu sayfasına git
                        self.driver.get(self.APPOINTMENT_URL)
                        time.sleep(3)
                        self._check_and_solve_captcha()
                        break
                else:
                    result['message'] = "Email onayı 5 dakika içinde yapılmadı"
                    return result

            # ── Şartlar/Onay sayfası var mı? ─────────────────────────────────
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            if any(kw in page_text.lower() for kw in [
                "accept", "agree", "terms", "şartlar", "kabul", "consent"
            ]):
                # "Go To Home" veya "Accept" butonuna tıkla
                accept_btn = self._find_visible_button([
                    "Accept", "Agree", "Kabul", "I Accept", "Go To Home", "Continue"
                ])
                if accept_btn:
                    logger.info(f"Onay sayfası — '{accept_btn.text.strip()}' tıklanıyor...")
                    self.driver.execute_script("arguments[0].click();", accept_btn)
                    time.sleep(2)
                    # Tekrar randevu sayfasına git
                    self.driver.get(self.APPOINTMENT_URL)
                    time.sleep(3)

            # ── Randevu sayfası içeriğini logla (debug) ───────────────────────
            try:
                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                logger.info(f"Randevu sayfası URL: {self.driver.current_url}")
                logger.debug(f"Sayfa içeriği (ilk 500): {page_text[:500]}")
            except Exception:
                page_text = ""

            # ── Başvuru profil formu doldurulmamış mı? ────────────────────────
            # "You have not filled out and completed the applicant detail form"
            if "have not filled out" in page_text.lower() or "complete the form" in page_text.lower():
                logger.warning(
                    "⚠️ Başvuru profil formu eksik! "
                    "'Click Here To Complete Application Form' butonuna tıklanıyor..."
                )
                # Butona tıkla
                complete_btn = self._find_visible_button([
                    "Click Here To Complete", "Complete Application", "Application Form",
                    "Complete Form", "Düzenle", "Edit"
                ])
                if complete_btn:
                    self.driver.execute_script("arguments[0].click();", complete_btn)
                    time.sleep(2)

                logger.warning(
                    "📋 Lütfen tarayıcıda açılan formu doldurun:\n"
                    "   • Surname At Birth, Place Of Birth\n"
                    "   • Country Of Birth, Current Nationality\n"
                    "   • Gender, Marital Status\n"
                    "   Formu kaydedince bot otomatik devam edecek (max 10 dk bekler)."
                )
                # Kullanıcı formu doldurana kadar bekle
                # Form kaydedilince URL değişir (appointment sayfasına döner)
                for _ in range(120):  # 10 dakika
                    time.sleep(5)
                    url_now = self.driver.current_url.lower()
                    body_now = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                    # Form sayfasından çıktık mı?
                    if ("have not filled out" not in body_now and
                            "edit member" not in body_now and
                            "personal details" not in body_now):
                        logger.info("✅ Profil formu tamamlandı, randevu kontrolüne devam ediliyor...")
                        # Tekrar randevu sayfasına git
                        self.driver.get(self.APPOINTMENT_URL)
                        time.sleep(3)
                        break
                else:
                    result['message'] = "Profil formu 10 dakika içinde tamamlanmadı"
                    return result

            # ── Randevu formunu doldur ────────────────────────────────────────
            categories_to_check = [c.strip() for c in self.user_data.get("category", "").split(",") if c.strip()]
            if not categories_to_check:
                logger.warning(f"[{self.user_data.get('first_name')}] Kategori girilmemiş.")
                result['message'] = "Kategori eksik"
                return result

            all_available_results = []
            
            for index, cat in enumerate(categories_to_check):
                logger.info(f"--- Kategori Kontrol Ediliyor: {cat} ---")
                
                if index > 0:
                    # Yeni kategori kontrolü öncesi, normal taramaya benzer bekleme koy
                    delay = int(self.user_data.get("check_interval", 60))
                    logger.info(f"[{self.user_data.get('first_name')}] Sıradaki kategori ({cat}) için {delay} saniye bekleniyor...")
                    time.sleep(delay)
                    
                    self.driver.get(self.APPOINTMENT_URL)
                    time.sleep(3)
                    self._check_and_solve_captcha()

                form_filled = self._fill_appointment_form(cat)
                
                # Bazen Submit ettikten sonra "Pending Appointment" hatası verir
                if self._handle_pending_appointment():
                    logger.info(f"  🔄 Pending silindiği için form {cat} için tekrar dolduruluyor...")
                    form_filled = self._fill_appointment_form(cat)

                if not form_filled:
                    if index == 0:
                        result['message'] = "Randevu formu doldurulamadı (Ayarları kontrol edin)"
                        logger.warning("Randevu formu eksik — ayarlar kontrol edilmeli")
                    continue

                # ── Post-form CAPTCHA kontrolü ──
                captcha_solved = False
                try:
                    logger.info("  Form sonrası CAPTCHA kontrolü yapılıyor...")
                    captcha_solved = self._check_and_solve_captcha()
                    if captcha_solved:
                        logger.info("  CAPTCHA çözüldü, takvim yüklenmesi bekleniyor...")
                        time.sleep(2)  # Takvimin yüklenmesi için ekstra bekleme
                except Exception as ce:
                    logger.warning(f"Post-submit CAPTCHA hatası: {ce}")

                # Eğer CAPTCHA çıktı ama çözülemediyse, tarih aramayı atla
                # (Aksi halde CAPTCHA sayfası 'randevu yok' olarak algılanır)
                from bot.captcha_solver import BLSCaptchaSolver
                post_solver = BLSCaptchaSolver(self.driver, api_key=self.config.get("2captcha_key", "").strip())
                if post_solver.is_captcha_present():
                    logger.warning(f"  ⚠️ CAPTCHA hala ekranda! ({cat}) — tarih araması atlanıyor.")
                    continue

                # Müsait tarih kontrolü
                available_dates = self._find_available_dates()
                if available_dates:
                    logger.info(f"RANDEVU BULUNDU ({cat}): {available_dates}")
                    for d in available_dates:
                        all_available_results.append({"category": cat, "day": d})
                else:
                    logger.info(f"Müsait randevu bulunamadı ({cat})")

            if all_available_results:
                result['available'] = True
                
                # En erken tarihe göre sırala
                all_available_results.sort(key=lambda x: int(x['day']) if str(x['day']).isdigit() else 999)
                
                # Sadece loglama ve bildirim iÃ§in gÃ¶rsel tarihleri hazÄ±rla
                formatted_dates = [f"{r['day']} ({r['category']})" for r in all_available_results]
                result['dates'] = formatted_dates
                
                # Auto book iÃ§in dict listesini de dÃ¶ndÃ¼relim
                result['raw_results'] = all_available_results
                
                result['message'] = f"{len(formatted_dates)} müsait tarih bulundu!"
                
                # Bildirimler multi-bot yöneticisi (Manager_ tarafindan yapilacak.
                # Scraper sadece sonucu döndürür.
                pass
            else:
                result['message'] = "Müsait randevu yok"
                logger.info(f"[{self.user_data.get('first_name')}] Müsait randevu bulunamadı")

        except TimeoutException:
            result['message'] = "Sayfa yüklenemedi (timeout)"
            logger.error("Randevu sayfası timeout")
            proxy_manager.report_failure(self.proxy)
        except WebDriverException as e:
            result['message'] = f"Tarayıcı hatası: {str(e)[:100]}"
            logger.error(f"WebDriver hatası: {e}")
            proxy_manager.report_failure(self.proxy)
        except Exception as e:
            result['message'] = f"Beklenmeyen hata: {str(e)[:100]}"
            logger.error(f"Beklenmeyen hata: {e}")
            proxy_manager.report_failure(self.proxy)

        # Report success if we reached here without raising an exception and we got a valid response type
        if result['available'] or "yok" in result['message'].lower():
             proxy_manager.report_success(self.proxy)

        return result

    def _fill_appointment_form(self, selected_category=None) -> bool:
        """
        Randevu formunu config'den okunan değerlerle doldur.
        Dinamik form yapısı nedeniyle Label bazlı seçim yapar.
        """
        from selenium.webdriver.common.keys import Keys

        jurisdiction = self.user_data.get("jurisdiction", "").strip()
        location     = self.user_data.get("location", "").strip()
        category     = selected_category or self.user_data.get("category", "").strip()
        category     = category.split(",")[0].strip() if "," in category and not selected_category else category
        appointment_for = self.user_data.get("appointment_for", "Individual")
        visa_type    = self.user_data.get("visa_type", "").strip()
        visa_sub_type = self.user_data.get("visa_sub_type", "").strip()

        if not jurisdiction or not visa_type:
            logger.warning("Randevu formu eksik: jurisdiction veya visa_type girilmemiş.")
            return False

        logger.info(f"Randevu formu dolduruluyor (Dinamik Label Yöntemi)")

        try:
            # ── 1. Helper: Select2 Seçimi (Updated Logic) ───────────────────
            def normalize_tr(text):
                if not text: return ""
                text = text.replace("İ", "i").replace("I", "ı").lower()
                return text.strip()

            def select2_pick(search_value: str, step_name: str) -> bool:
                """Açık listeden seçim yapar (Standart + Generic LI Fallback)"""
                try:
                    # Arama kutusu
                    try:
                        sb = self.driver.find_element(By.CSS_SELECTOR, ".select2-search__field")
                        sb.clear()
                        sb.send_keys(search_value)
                        time.sleep(0.3)
                    except: pass

                    search_norm = normalize_tr(search_value)

                    # A. Standart
                    opts = self.driver.find_elements(By.CSS_SELECTOR, ".select2-results__option:not(.select2-results__option--disabled)")
                    visible = [o for o in opts if o.is_displayed()]
                    if visible:
                        opts_text = [o.text.strip() for o in visible[:5]]
                        logger.info(f"  [DEBUG] {step_name} choices: {opts_text}")
                        # Tam Eşleşme
                        for o in visible:
                            if normalize_tr(o.text) == search_norm:
                                self.driver.execute_script("arguments[0].click();", o)
                                logger.info(f"  ✓ {step_name}: {o.text} (Tam)")
                                time.sleep(0.2)
                                return True
                        # Kısmi
                        for o in visible:
                            if search_norm in normalize_tr(o.text):
                                self.driver.execute_script("arguments[0].click();", o)
                                logger.info(f"  ✓ {step_name}: {o.text} (Kısmi)")
                                time.sleep(0.2)
                                return True

                    # B. Generic Li Fallback
                    logger.info(f"  [DEBUG] {step_name}: Standart yok, genel LI aranıyor...")
                    all_lis = self.driver.find_elements(By.TAG_NAME, "li")
                    vis_lis = [li for li in all_lis if li.is_displayed() and li.text.strip()]
                    for li in vis_lis:
                        if search_norm in normalize_tr(li.text):
                            self.driver.execute_script("arguments[0].click();", li)
                            logger.info(f"  ✓ {step_name}: {li.text} (Fallback)")
                            time.sleep(0.2)
                            return True
                    
                    logger.warning(f"  {step_name}: '{search_value}' bulunamadı.")
                    return False
                except Exception as e:
                    logger.debug(f"  select2_pick error: {e}")
                return False

            # ── 2. Helper: Label Bazlı Tıklama (Hibrit: DOM + Koordinat) ────────
            def click_container_by_keywords(keywords: list) -> bool:
                """
                Verilen keyword'leri içeren Label'ı bul.
                DOM ve Koordinat yöntemleri ile en uygun kutuyu seç.
                """
                try:
                    # 1. Label Bul
                    labels = self.driver.find_elements(By.TAG_NAME, "label")
                    target = None
                    for lbl in labels:
                        if not lbl.is_displayed(): continue
                        txt = lbl.text.lower()
                        if any(k.lower() in txt for k in keywords):
                            target = lbl
                            break
                    
                    if not target:
                        logger.warning(f"  Label bulunamadı: {keywords}")
                        return False

                    l_loc = target.location
                    l_y = l_loc['y']
                    best_container = None
                    logger.info(f"  [DEBUG] Hedef Label: '{target.text}' Y={l_y}")

                    # ── STRATEJI A: Global Koordinat (Genişletilmiş) ──
                    # Label'a fiziksel olarak en yakın olan kutucuğu seç (Çok daha hızlı ve güvenilir)
                    logger.info("  Koordinat bazlı container araması yapılıyor...")
                        
                    # Daha geniş selector listesi (+ Kendo UI)
                    selectors = [
                        ".select2-selection", ".select2-selection--single", 
                        "span.select2-container", "div.select2-container",
                        "[class*='select2-selection']", "[class*='select2-container']",
                        ".k-dropdown", ".k-input", "[role='listbox']",
                        "select"
                    ]
                    all_cands = []
                    for sel in selectors:
                        all_cands.extend(self.driver.find_elements(By.CSS_SELECTOR, sel))
                    
                    # Analiz ve Filtreleme
                    uniq_els = {el.id: el for el in all_cands}.values()
                    valid_candidates = []
                    
                    for c in uniq_els:
                        try:
                            c_loc = c.location
                            c_y = c_loc['y']
                            visible = c.is_displayed()
                            tag_name = c.tag_name
                            cls_name = c.get_attribute("class")
                            
                            # Log (Commented out again)
                            # logger.info(f"    [ADAY] Tag={tag_name} Class='{cls_name}' Y={c_y} Vis={visible}")

                            if not visible: continue
                            
                            # Tolerans: -20px yukarı, +300px aşağı
                            if c_y >= l_y - 20:
                                dist = c_y - l_y
                                if dist < 300:
                                    valid_candidates.append((dist, c))
                        except Exception as e:
                            logger.debug(f"    [SKIP] Hata: {e}")
                    
                    if valid_candidates:
                        valid_candidates.sort(key=lambda x: x[0])
                        best_container = valid_candidates[0][1]
                        logger.info(f"  Container bulundu (Coord Strategy) - Fark: {valid_candidates[0][0]}px")

                    # ── STRATEJI B: JS Element From Point (Viewport Coord) ──
                    if not best_container:
                        logger.info("  Selector Strategy başarısız, JS elementFromPoint (Viewport) deneniyor...")
                        try:
                            # Label'ı ekrana getir (Scroll)
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
                            time.sleep(0.5)
                            
                            # Viewport koordinatlarını al
                            rect = self.driver.execute_script("return arguments[0].getBoundingClientRect();", target)
                            start_x = rect['x']
                            start_y = rect['y'] + rect['height']
                            
                            logger.info(f"    [JS-Probe] Viewport Coords: Label Rect: {rect}")
                            
                            # Label'ın solundan 20px içeride, altından 15-35px aşağıda tara
                            test_offsets = [15, 25, 35, 45] 
                            
                            for off in test_offsets:
                                tx = start_x + 20
                                ty = start_y + off
                                el = self.driver.execute_script("return document.elementFromPoint(arguments[0], arguments[1]);", tx, ty)
                                
                                if el:
                                    tag = el.tag_name.lower()
                                    cls = el.get_attribute("class") or ""
                                    logger.info(f"    [JS-Probe] ({tx},{ty}) -> <{tag} class='{cls}'>")
                                    
                                    if tag in ["html", "body", "form"]: continue
                                    
                                    best_container = el
                                    break
                        except Exception as e:
                            logger.error(f"  JS Probe Error: {e}")

                    if not best_container:
                         logger.warning(f"  Container HİÇBİR YÖNTEMLE bulunamadı (Label: {keywords[0]})")
                         return False

                    # Tıkla
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", best_container)
                    time.sleep(0.1)
                    try:
                        best_container.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", best_container)
                    
                    time.sleep(0.2)
                    return True

                except Exception as e:
                    logger.debug(f"  click_container error: {e}")
                return False

            # ── 3. Step-by-Step Filling (with retry on label-not-found) ─────
            
            max_form_retries = 3
            for form_attempt in range(max_form_retries):
                if form_attempt > 0:
                    logger.warning(f"  🔄 Form doldurma tekrar deneniyor... (Deneme {form_attempt + 1}/{max_form_retries})")
                    self.driver.get(self.APPOINTMENT_URL)
                    time.sleep(3)
                    self._check_and_solve_captcha()
                
                def _try_pick(keywords, val, name, mandatory=True):
                    if not val:
                        return True
                    for attempt in range(4): # 4 deneme (yaklaşık 6s)
                        if click_container_by_keywords(keywords):
                            time.sleep(0.5)
                            if select2_pick(val, name):
                                return True
                        logger.warning(f"  ⏳ {name} açılması bekleniyor... (Deneme {attempt+1}/4)")
                        time.sleep(1.5)
                    
                    # Eğer 4 denemede de olmadıysa
                    if mandatory:
                        logger.error(f"  ❌ {name} seçilemedi (sayfa tam yüklenmemiş veya eleman yok).")
                    else:
                        logger.warning(f"  ⚠️ {name} seçilemedi, ama opsiyonel, devam ediliyor.")
                    return not mandatory

                # A) Jurisdiction
                logger.info(f"  → Jurisdiction: {jurisdiction}")
                if not _try_pick(["Jurisdiction", "İl", "City"], jurisdiction, "Jurisdiction", mandatory=True):
                    if form_attempt < max_form_retries - 1: continue
                    else: return False
                time.sleep(0.3)

                # B) Appointment For (Radio) - Arayüzde yer değişebilir, tekrar bulalım
                for radio_attempt in range(4):
                    try:
                        radios = self.driver.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                        clicked_radio = False
                        for r in radios:
                            val = (r.get_attribute("value") or "").lower()
                            lbl_txt = ""
                            try:
                                lbl = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{r.get_attribute('id')}']")
                                lbl_txt = lbl.text.lower()
                            except: pass
                            
                            needed = appointment_for.lower()
                            if needed in val or needed in lbl_txt:
                                try:
                                    self.driver.execute_script("arguments[0].click();", r)
                                    clicked_radio = True
                                    break
                                except: pass

                        if clicked_radio:
                            logger.info(f"  ✓ Appointment For: {appointment_for}")
                            break
                        else:
                            logger.warning(f"  ⏳ Appointment For bekleniyor... ({radio_attempt+1}/4)")
                            time.sleep(1.5)
                    except: 
                        time.sleep(1.5)
                time.sleep(0.2)

                # C) Location (Opsiyonel / Dinamik)
                if location:
                    logger.info(f"  → Location: {location}")
                    if not _try_pick(["Location", "Konum"], location, "Location", mandatory=True):
                        if form_attempt < max_form_retries - 1: continue
                        else: return False
                    time.sleep(0.3)

                # D) Visa Type
                logger.info(f"  → Visa Type: {visa_type}")
                if not _try_pick(["Visa Type", "Vize Türü"], visa_type, "Visa Type", mandatory=True):
                    if form_attempt < max_form_retries - 1: continue
                    else: return False
                time.sleep(0.3)

                # E) Visa Sub Type (Opsiyonel)
                if visa_sub_type:
                    logger.info(f"  → Sub Type: {visa_sub_type}")
                    _try_pick(["Sub Type", "Alt Tür"], visa_sub_type, "Sub Type", mandatory=False)
                    time.sleep(0.3)

                # F) Category
                if category:
                    logger.info(f"  → Category: {category}")
                    _try_pick(["Category", "Kategori"], category, "Category", mandatory=False)
                    time.sleep(0.5)
                    self._handle_premium_popup()
                    time.sleep(0.5)

                # Submit
                btn = self._find_visible_button(["Submit", "submit", "Ara", "Search"])
                if btn:
                    logger.info("  Submit ediliyor...")
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3)
                
                return True  # Form successfully filled

            # All retries exhausted (shouldn't reach here, but safety net)
            return False

        except Exception as e:
            logger.error(f"Form hatası: {e}", exc_info=True)
            return False

    def _handle_premium_popup(self):
        """Premium kategori seçilince çıkan popup'ı onayla"""
        try:
            # 1. Popup var mı kontrol et
            # Genelde class: "modal", "popup", id: "commonModal" veya metin içerir
            page_source = self.driver.page_source.lower()
            if "premium category confirmation" not in page_source and "premium lounge" not in page_source:
                return 

            logger.info("💎 Premium Popup tespit edildi! Onaylanıyor...")

            # 2. Butonu bul (Accept/Onayla) - Genelde yeşil veya 'btn-success'
            # Screenshot'ta "Accept" yeşil buton
            
            # Seçiciler
            selectors = [
                "//button[contains(text(), 'Accept') or contains(text(), 'Kabul')]",
                "//a[contains(text(), 'Accept') or contains(text(), 'Kabul')]",
                "//button[contains(@class, 'btn-success')]",
                "//button[contains(@class, 'btn-primary')]" # Bazen kırmızı 'Reject' primary olabilir, dikkat!
            ]
            
            for xpath in selectors:
                try:
                    btns = self.driver.find_elements(By.XPATH, xpath)
                    for btn in btns:
                        if not btn.is_displayed(): continue
                        txt = btn.text.lower()
                        
                        # Danger/Reject olmasın
                        if "reject" in txt or "reddet" in txt or "iptal" in txt: continue
                        
                        # Accept veya Lounge içeriyorsa bas
                        if "accept" in txt or "kabul" in txt or "ok" in txt or "yes" in txt:
                            logger.info(f"💎 Premium Onay butonu tıklandı: {txt}")
                            self.driver.execute_script("arguments[0].click();", btn)
                            time.sleep(1.5)
                            return
                except: pass
            
            logger.warning("💎 Premium Popup var ama 'Accept' butonu bulunamadı!")
            
        except Exception as e:
            logger.debug(f"Premium popup hatası: {e}")

    def _handle_pending_appointment(self) -> bool:
        """
        'Book New Appointment - Appointment Pending' sayfasını tespit edip otomatik temizler.
        
        Sayfa 1 — URL: /pendingappointment?err=...
          → 'DeletePendingAppointment' butonuna tıkla
        Sayfa 2 — URL: /PendingAppointment  (büyük P)
          → 'PendingAppointmentRemoved' mesajı görünür  
          → 'Book New Appointment' butonuna tıkla
          
        Returns: True eğer pending randevu silindi ve yeni randevu sayfasına geçildi.
                 False eğer bu sayfa tespit edilmedi (normal akış devam eder).
        """
        try:
            url = self.driver.current_url.lower()
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            page_lower = page_text.lower()

            # Ekran 1: "Appointment Pending" sayfası
            if "pendingappointment" in url or "appointment pending" in page_lower or "deletependingappointment" in page_lower:
                logger.warning("⚠️ Bekleyen randevu tespit edildi! Siliniyor...")

                # 'DeletePendingAppointment' butonunu bul ve tıkla
                deleted = False
                delete_texts = [
                    "DeletePendingAppointment",
                    "Delete Pending",
                    "Bekleyen Randevuyu Sil",
                ]
                for text in delete_texts:
                    try:
                        btns = self.driver.find_elements(By.XPATH,
                            f"//button[contains(text(), '{text}')] | //a[contains(text(), '{text}')] | //input[@value='{text}']"
                        )
                        for btn in btns:
                            if btn.is_displayed():
                                self.driver.execute_script("arguments[0].click();", btn)
                                logger.info(f"  ✅ '{text}' tıklandı.")
                                deleted = True
                                time.sleep(2)
                                break
                    except Exception:
                        pass
                    if deleted:
                        break

                if not deleted:
                    # Son çare: sarı/turuncu buton
                    try:
                        btns = self.driver.find_elements(By.CSS_SELECTOR,
                            "a.btn-warning, button.btn-warning, a.btn-danger, button.btn-danger"
                        )
                        for btn in btns:
                            if btn.is_displayed() and "cancel" not in btn.text.lower():
                                self.driver.execute_script("arguments[0].click();", btn)
                                logger.info(f"  ✅ Pending silme butonu (CSS fallback) tıklandı: {btn.text}")
                                deleted = True
                                time.sleep(2)
                                break
                    except Exception:
                        pass

                if not deleted:
                    logger.error("  ❌ DeletePendingAppointment butonu bulunamadı!")
                    return False

                # Ekran 2: "Pending Appointment Removed" onayı geldi mi?
                time.sleep(1)
                new_page = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                if "pendingappointmentremoved" in new_page or "removed" in new_page or "book new appointment" in new_page:
                    logger.info("  ✅ Bekleyen randevu silindi. 'Book New Appointment' tıklanıyor...")
                    # "Book New Appointment" butonuna tıkla
                    btn = self._find_visible_button(["Book New Appointment", "Yeni Randevu"])
                    if btn:
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(3)
                        logger.info("  ✅ Yeni randevu sayfasına yönlendirildi.")
                    return True

            return False  # Bu sayfa değil, normal akış devam etsin

        except Exception as e:
            logger.debug(f"Pending appointment kontrolü hatası: {e}")
            return False

    # ... (Rest of format methods) ...

    def _select_dropdown_by_text(self, label: str, value: str) -> bool:
        """
        Select2 veya native select dropdown'ından kısmi metin eşleşmesiyle seçim yapar.
        3 strateji dener: JS val+trigger, Select2 UI tıklama, native select.
        """
        from selenium.webdriver.common.keys import Keys
        label_lower = label.lower().replace(" ", "")

        # ── Strateji 1: JS ile Select2'yi programatik olarak tetikle ──────────
        # Sayfadaki tüm <select> elementlerini bul, label'a göre eşleştir
        try:
            result = self.driver.execute_script("""
                var labelLower = arguments[0];
                var valueLower = arguments[1];

                // Tüm select elementlerini tara
                var selects = document.querySelectorAll('select');
                for (var sel of selects) {
                    // Select'in ID/name/placeholder'ı label ile eşleşiyor mu?
                    var selId = (sel.id || '').toLowerCase().replace(/[^a-z]/g,'');
                    var selName = (sel.name || '').toLowerCase().replace(/[^a-z]/g,'');

                    // Yakın label elementini bul
                    var labelText = '';
                    var parent = sel.closest('.form-group, .col-md-4, .col-md-6, div');
                    if (parent) {
                        var lbl = parent.querySelector('label, .control-label');
                        if (lbl) labelText = lbl.innerText.toLowerCase().replace(/[^a-z]/g,'');
                    }

                    var matches = selId.includes(labelLower) ||
                                  selName.includes(labelLower) ||
                                  labelText.includes(labelLower);

                    if (!matches) continue;

                    // Seçenekleri tara
                    for (var opt of sel.options) {
                        if (opt.text.toLowerCase().includes(valueLower)) {
                            // Select2 varsa jQuery ile tetikle
                            sel.value = opt.value;
                            if (window.jQuery && window.jQuery(sel).data('select2')) {
                                window.jQuery(sel).val(opt.value).trigger('change');
                                return 'select2:' + opt.text;
                            } else {
                                // Native change event
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                return 'native:' + opt.text;
                            }
                        }
                    }
                }
                return null;
            """, label_lower, value.lower())

            if result:
                logger.info(f"  {label}: '{result}' seçildi (JS)")
                
                # Check popup if Category was selected via JS
                if "category" in label_lower:
                    time.sleep(1.0)
                    self._handle_premium_popup()

                time.sleep(1)
                return True
        except Exception as e:
            logger.debug(f"JS Select2 hatası ({label}): {e}")

        # ── Strateji 2: Select2 UI — span container'a tıkla, ara, seç ────────
        try:
            # Select2 container'larını bul
            containers = self.driver.find_elements(
                By.CSS_SELECTOR,
                "span.select2-container, .select2-selection"
            )
            for container in containers:
                try:
                    # Container'ın yakınındaki label'ı kontrol et
                    parent = container.find_element(By.XPATH, "./ancestor::*[contains(@class,'form-group') or contains(@class,'col-')][1]")
                    lbl_el = parent.find_element(By.CSS_SELECTOR, "label, .control-label")
                    lbl_text = lbl_el.text.lower().replace(" ", "")
                    if label_lower not in lbl_text:
                        continue
                except Exception:
                    # Label bulunamazsa container text'e bak
                    if label.lower() not in container.text.lower():
                        continue

                # Container'a tıkla
                self.driver.execute_script("arguments[0].click();", container)
                time.sleep(0.8)

                # Arama kutusu
                try:
                    search = self.driver.find_element(By.CSS_SELECTOR, ".select2-search__field")
                    search.clear()
                    search.send_keys(value)
                    time.sleep(1.2)

                    # Sonuçları tara
                    options = self.driver.find_elements(
                        By.CSS_SELECTOR, ".select2-results__option:not(.select2-results__option--disabled)"
                    )
                    for opt in options:
                        if value.lower() in opt.text.lower() and opt.is_displayed():
                            opt.click()
                            logger.info(f"  {label}: '{opt.text}' seçildi (Select2 UI)")
                            
                            # Check popup if Category was selected via UI
                            if "category" in label_lower:
                                time.sleep(1.0)
                                self._handle_premium_popup()

                            time.sleep(0.5)
                            return True

                    # Sonuç bulunamadı, kapat
                    search.send_keys(Keys.ESCAPE)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Select2 UI hatası ({label}): {e}")

        # ── Strateji 3: Native select — tüm select'leri dene ─────────────────
        try:
            selects = self.driver.find_elements(By.TAG_NAME, "select")
            for sel_el in selects:
                if not sel_el.is_displayed():
                    continue
                sel_id = (sel_el.get_attribute("id") or "").lower()
                sel_name = (sel_el.get_attribute("name") or "").lower()
                if label_lower not in sel_id and label_lower not in sel_name:
                    continue
                sel = Select(sel_el)
                for opt in sel.options:
                    if value.lower() in opt.text.lower():
                        sel.select_by_visible_text(opt.text)
                        logger.info(f"  {label}: '{opt.text}' seçildi (native fallback)")
                        
                        # Check popup if Category was selected via Native
                        if "category" in label_lower:
                            time.sleep(1.0)
                            self._handle_premium_popup()
                        
                        return True
        except Exception as e:
            logger.debug(f"Native select hatası ({label}): {e}")

        logger.error(f"  {label}: '{value}' seçilemedi — tüm stratejiler başarısız")
        return False

    def _check_and_solve_captcha(self) -> bool:
        """
        Sayfada CAPTCHA var mı kontrol et, varsa çöz.
        Her sayfa yüklemesinden sonra çağrılabilir.
        Returns: True if CAPTCHA was found and solved, False otherwise.
        """
        try:
            from bot.captcha_solver import CaptchaSolver, BLSCaptchaSolver
            api_key = self.config.get("2captcha_key", "").strip()
            
            if not api_key:
                return False  # Cannot solve captcha without an API key

            solver = BLSCaptchaSolver(self.driver, api_key=api_key)
            
            if not solver.is_captcha_present():
                return False  # No CAPTCHA on page
            
            # CAPTCHA detected — now log and solve
            logger.info("🔐 CAPTCHA tespit edildi, 2Captcha ile çözülüyor...")
            METRIC_CAPTCHAS_ENCOUNTERED.inc()
            report_account_risk(self.user_data.get('id'), 5, reason="Captcha Çıktı")

            success = solver.solve()
            
            # VERİFICATION: Çözüm sonrası Captcha hala orada mı?
            time.sleep(2)
            if solver.is_captcha_present():
                 logger.warning("⚠️ Captcha çözüldü dendi ama HALA VAR! (Yanlış çözüm?)")
                 logger.info("   Tekrar deneniyor (Retry)...")
                 time.sleep(2)
                 success_retry = solver.solve()
                 if success_retry:
                     logger.info("   ✅ İkinci denemede çözüldü.")
                 else:
                     logger.error("   ❌ İkinci deneme de başarısız.")
                     return False
            else:
                if success:
                    logger.info("✅ CAPTCHA başarıyla geçildi (Artık ekranda yok).")
                else:
                     # Çözülemedi ama ekranda da yok? Belki de geçildi?
                     logger.warning("Captcha çözülemedi döndü ama ekranda bulunamadı. Devam ediliyor.")
            
            # Post-solve bekleme: sayfa yenilensin / takvim yüklensin
            time.sleep(3)
            return True
        except Exception as e:
            logger.debug(f"CAPTCHA kontrol hatası: {e}")
            return False

    def _find_available_dates(self) -> list:
        """
        Sayfadaki MÜSAİT (yeşil) tarihleri bul.
        Ekranda: Yeşil = Appointment Available, Kırmızı = No Slot Available
        Sadece yeşil hücreleri döndürür.
        """
        available = []
        min_days = int(self.config.get("minimum_days", 0))

        def _is_valid_date(day_text):
            if min_days <= 0:
                return True
            try:
                from datetime import datetime
                day = int(day_text)
                today = datetime.now()
                if day < today.day:
                    target_month = today.month + 1 if today.month < 12 else 1
                    target_year = today.year if today.month < 12 else today.year + 1
                else:
                    target_month = today.month
                    target_year = today.year
                target_date = datetime(target_year, target_month, day)
                delta = (target_date.date() - today.date()).days
                return delta >= min_days
            except Exception:
                return True

        # ── Yöntem 1: 'available' class'ına sahip takvim hücreleri ──────────
        # Sitenin kullandığı CSS class'larını öncelik sırasıyla dene
        available_selectors = [
            "td.day.available:not(.disabled):not(.old):not(.new)",  # Bootstrap datepicker'ın standart class'ı
            "td.day.green:not(.disabled)",                          # Özel green class
            "td[class*='available']:not([class*='no-slot'])",       # 'available' içeren genel
        ]

        for selector in available_selectors:
            try:
                cells = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if cells:
                    for cell in cells:
                        text = cell.text.strip()
                        if text and text.isdigit():
                            if _is_valid_date(text):
                                available.append(text)
                            else:
                                logger.info(f"  {text}. gün müsait ama {min_days} günden yakın, es geçiliyor.")
                    if available:
                        logger.info(f"  Müsait tarihler ({selector}): {available}")
                        return available
            except Exception:
                pass

        # ── Yöntem 2: JS ile arka plan rengini kontrol et ───────────────────
        # Yeşil = rgb(40, 167, 69) veya benzeri #28a745, #20c997 vs.
        # Kırmızı = rgb(220, 53, 69) = #dc3545
        try:
            all_cells = self.driver.find_elements(
                By.CSS_SELECTOR,
                "td.day:not(.disabled):not(.old):not(.new)"
            )
            for cell in all_cells:
                text = cell.text.strip()
                if not text or not text.isdigit():
                    continue
                try:
                    bg = self.driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).backgroundColor;",
                        cell
                    )
                    # Yeşil tonları: rgb(40, ...) veya rgb değerinde green > red
                    if bg:
                        parts = [int(x) for x in bg.replace("rgba(","").replace("rgb(","").replace(")","").split(",")[:3]]
                        r, g, b = parts[0], parts[1], parts[2]
                        # Yeşil: g dominant (g > 100 ve g > r)
                        is_green = g > 100 and g > r
                        if is_green and _is_valid_date(text):
                            available.append(text)
                except Exception:
                    pass
            if available:
                logger.info(f"  Müsait tarihler (JS renk analizi): {available}")
                return available
        except Exception:
            pass

        # ── Yöntem 3: Dropdown tarih listesi (eğer takvim değil select ise) ─
        try:
            date_select = self.driver.find_element(By.ID, "AppointmentDate")
            options = Select(date_select).options
            for opt in options[1:]:
                if opt.get_attribute("value"):
                    available.append(opt.text.strip())
            if available:
                return available
        except Exception:
            pass

        logger.info("  Müsait tarih bulunamadı.")
        return []

    def _find_available_slots(self) -> list:
        """
        Appointment Slot dropdown'ından sadece yeşil (Slot Available) seçenekleri döndürür.
        Kırmızı (Slot Booked) olanları atlar.
        """
        available_slots = []
        try:
            # Önce native select elementini dene
            slot_select = self.driver.find_element(By.ID, "TimeSlot")
            options = Select(slot_select).options
            for opt in options[1:]:
                val = opt.get_attribute("value")
                if not val:
                    continue
                # JS ile arka plan rengini kontrol et
                try:
                    bg = self.driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).backgroundColor;",
                        opt
                    )
                    if bg:
                        parts = [int(x) for x in bg.replace("rgba(","").replace("rgb(","").replace(")","").split(",")[:3]]
                        r, g, b = parts[0], parts[1], parts[2]
                        is_red = r > 150 and r > g * 1.5   # Kırmızı = dolu slot
                        if is_red:
                            logger.debug(f"  Slot dolu (kırmızı): {opt.text}")
                            continue
                except Exception:
                    pass  # Renk okunamazsa yine ekle (güvenli taraf)
                available_slots.append({"value": val, "text": opt.text.strip()})

        except Exception:
            # Alternatif: CSS class bazlı slot li elementleri (custom dropdown ise)
            try:
                slot_items = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "ul.slot-list li.available, li.slot-available, .slot-item:not(.booked)"
                )
                for item in slot_items:
                    val = item.get_attribute("data-value") or item.text.strip()
                    if val:
                        available_slots.append({"value": val, "text": item.text.strip()})
            except Exception:
                pass

        return available_slots

    def book_appointment(self, target_slot=None) -> bool:
        """
        Randevu al (ilk müsait tarihe veya verilen hedefe).
        Returns: True if successful
        """
        try:
            target_date = None
            target_category = None
            if isinstance(target_slot, dict):
                target_date = target_slot.get("day")
                target_category = target_slot.get("category")
            else:
                target_date = target_slot

            logger.info(f"Randevu alınıyor: {target_date or 'ilk müsait'} (Kategori: {target_category or 'MEVCUT'})")

            # Eğer özel bir kategori seçildiyse (çoklu kategori taramasından geliyorsa), formu tazeleyip seçelim
            if target_category:
                logger.info(f"Hedef kategori ({target_category}) için form yeniden dolduruluyor...")
                self.driver.get(self.APPOINTMENT_URL)
                time.sleep(3)
                self._check_and_solve_captcha()
                form_filled = self._fill_appointment_form(target_category)
                
                # Bazen Submit ettikten sonra "Pending Appointment" hatası verir
                if self._handle_pending_appointment():
                    logger.info("  🔄 Pending silindiği için rezervasyon formu tekrar dolduruluyor...")
                    form_filled = self._fill_appointment_form(target_category)

                if not form_filled:
                    logger.error("Rezervasyon için form doldurulamadı!")
                    return False
                time.sleep(2)
                self._check_and_solve_captcha()

            # ── Tarih seçimi ─────────────────────────────────────────────────
            if target_date:
                date_clicked = False
                # Önce sadece yeşil (müsait) hücreler arasında ara
                for selector in [
                    "td.day.available:not(.disabled):not(.old):not(.new)",
                    "td.day:not(.disabled):not(.old):not(.new)",
                ]:
                    try:
                        cells = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        for cell in cells:
                            if cell.text.strip() == str(target_date):
                                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cell)
                                time.sleep(0.2)
                                self.driver.execute_script("arguments[0].click();", cell)
                                logger.info(f"  ✅ Tarih seçildi: {target_date}")
                                date_clicked = True
                                time.sleep(1.5)  # Slot dropdown'ın yüklenmesi için bekle
                                break
                    except Exception:
                        pass
                    if date_clicked:
                        break

                if not date_clicked:
                    logger.warning(f"  ⚠️ {target_date}. gün takvimde bulunamadı!")

            # ── Zaman slotu seç (sadece yeşil/müsait olanlar) ───────────────
            slot_selected = False
            try:
                # Slot dropdown'ının belirmesini bekle
                self.wait.until(EC.presence_of_element_located((By.ID, "TimeSlot")))
                time.sleep(0.5)
                
                # Renk bazlı filtreleme: sadece yeşil slotları getir
                available_slots = self._find_available_slots()
                
                if available_slots:
                    logger.info(f"  Müsait slotlar: {[s['text'] for s in available_slots]}")
                    first_slot = available_slots[0]
                    time_select = self.driver.find_element(By.ID, "TimeSlot")
                    Select(time_select).select_by_value(first_slot["value"])
                    logger.info(f"  ✅ Slot seçildi: {first_slot['text']}")
                    slot_selected = True
                    time.sleep(1)
                else:
                    # Fallback: ilk değerli seçenek (güvenli taraf)
                    logger.warning("  Renk bazlı slot bulunamadı, ilk değerli seçeneğe düşülüyor...")
                    time_select = self.driver.find_element(By.ID, "TimeSlot")
                    for opt in Select(time_select).options[1:]:
                        if opt.get_attribute("value"):
                            Select(time_select).select_by_value(opt.get_attribute("value"))
                            logger.info(f"  Slot (fallback): {opt.text}")
                            slot_selected = True
                            break
                    time.sleep(1)
            except Exception as e:
                logger.warning(f"  Zaman slotu seçilemedi: {e}")

            if not slot_selected:
                logger.error("  Hiçbir müsait slot seçilemedi!")
                return False

            # ── Submit ───────────────────────────────────────────────────────
            try:
                confirm_btn = self.wait.until(
                    EC.element_to_be_clickable((By.ID, "btnSubmit"))
                )
                self.driver.execute_script("arguments[0].click();", confirm_btn)
                time.sleep(3)
            except Exception:
                # Alternatif buton
                btns = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    btn_text = btn.text.lower()
                    if any(kw in btn_text for kw in ["submit", "confirm", "onayla", "devam"]):
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(3)
                        break

            # ── Başarı kontrolü ──────────────────────────────────────────────
            page_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            success_keywords = ["success", "confirmed", "başarı", "onaylandı", "appointment booked"]
            if any(kw in page_text for kw in success_keywords):
                logger.info("🎉 Randevu başarıyla alındı!")
                return True
            else:
                logger.warning("Randevu alındı mı belirsiz, sayfa kontrol edilmeli")
                return True  # Kullanıcı tarayıcıdan görebilir

        except Exception as e:
            logger.error(f"Randevu alma hatası: {e}")
            return False

    def is_driver_alive(self) -> bool:
        """WebDriver hâlâ çalışıyor mu?"""
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False
