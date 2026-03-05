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
    def __init__(self, user_data: dict, global_config: dict = None, log_func=None):
        self.user_data = user_data
        self.config = global_config or {}
        self.headless = bool(self.user_data.get('headless', True))
        self._custom_log = log_func
        
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
    def _log(self, level, msg):
        if self._custom_log:
            self._custom_log(level, msg)
        else:
            logger.log(level, f"[{self.user_data.get('first_name', 'Bilinmiyor')}] {msg}")
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
                self._log(logging.INFO, f"Gizli (Stealth Headless) modda başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
                import undetected_chromedriver as uc
                import re
                
                max_retries = 3
                
                # Proactively detect installed Chrome version to avoid mismatch
                version_main = None
                try:
                    import subprocess
                    reg_result = subprocess.run(
                        ['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
                        capture_output=True, text=True, timeout=5
                    )
                    match = re.search(r'(\d+)\.', reg_result.stdout)
                    if match:
                        version_main = int(match.group(1))
                        self._log(logging.INFO, f"Chrome v{version_main} algılandı, uyumlu driver kullanılacak.")
                except Exception:
                    pass
                
                for attempt in range(max_retries):
                    options = uc.ChromeOptions()
                    options.add_argument("--no-sandbox")
                    options.add_argument("--disable-dev-shm-usage")
                    options.add_argument(f"--window-size={res}")
                    options.add_argument("--disable-extensions")
                    options.add_argument("--disable-background-networking")
                    options.add_argument("--disable-default-apps")
                    options.add_argument("--mute-audio")
                    options.add_argument("--js-flags=--max-old-space-size=256")
                    
                    if self.proxy:
                        if "@" in self.proxy:
                            from bot.proxy_auth import create_proxy_extension
                            ext_path = create_proxy_extension(self.proxy)
                            if ext_path:
                                options.add_extension(ext_path)
                                if attempt == 0: self._log(logging.INFO, "Auth-Proxy Eklentisi Yüklendi.")
                        else:
                            options.add_argument(f"--proxy-server={self.proxy}")
                            if attempt == 0: self._log(logging.INFO, f"Proxy Aktif: {self.proxy}")
                    
                    options.add_argument(f"user-agent={ua}")
                    options.add_argument("--disable-gpu")
                    options.add_argument("--enable-javascript")
                    
                    try:
                        kwargs = {"options": options, "headless": True, "use_subprocess": True}
                        if version_main:
                            kwargs["version_main"] = version_main
                            
                        self.driver = uc.Chrome(**kwargs)
                        break
                    except Exception as try_err:
                        if attempt == max_retries - 1:
                            raise try_err
                        
                        err_str = str(try_err)
                        match = re.search(r"Current browser version is (\d+)", err_str)
                        if match:
                            version_main = int(match.group(1))
                            self._log(logging.INFO, f"Driver version mismatch. Forcing version_main={version_main}, clearing cache...")
                            # Clear stale cached chromedriver to force fresh download
                            import shutil, pathlib
                            cache_dir = pathlib.Path.home() / "appdata" / "roaming" / "undetected_chromedriver"
                            if cache_dir.exists():
                                shutil.rmtree(cache_dir, ignore_errors=True)
                            
                        self._log(logging.WARNING, f"Headless tarayıcı çöktü, tekrar deneniyor ({attempt+1}/{max_retries}): {try_err}")
                        time.sleep(2)
            else:
                self._log(logging.INFO, f"Normal (Görünür) Chrome başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
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
                            self._log(logging.INFO, "Auth-Proxy Eklentisi Yüklendi.")
                    else:
                        options.add_argument(f"--proxy-server={self.proxy}")
                        self._log(logging.INFO, f"Proxy Aktif: {self.proxy}")
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
                self._log(logging.INFO, "Stealth JS Enjekte Edildi.")
            except ImportError:
                self._log(logging.WARNING, "selenium-stealth kütüphanesi bulunamadı, standart ayarlar ile devam ediliyor.")
                
            self.wait = WebDriverWait(self.driver, 20)
            self._log(logging.INFO, "Chrome WebDriver başlatıldı (Network Loglama Aktif)")
            return True
        except Exception as e:
            self._log(logging.ERROR, f"WebDriver başlatma hatası: {e}")
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
                        filtered_logs.append(msg) # Ã…imdilik hepsini alalım, endpoint'i kaçırmayalım
                except: pass
            
            with open("network_activity.json", "w", encoding="utf-8") as f:
                json.dump(filtered_logs, f, indent=2)
            
            logger.info(f"✅ Network logları kaydedildi: network_activity.json ({len(filtered_logs)} olay)")
        except Exception as e:
            logger.error(f"Log dump hatası: {e}")
    def stop_driver(self):
        """WebDriver'ı kapat Ã¢â‚¬â€ çıkmadan önce cookie'leri kaydet"""
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
            self._log(logging.INFO, "WebDriver kapatıldı")
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
            self._log(logging.INFO, f"🔍 {len(cookies)} cookie kaydedildi: {self._session_file}")
        except Exception as e:
            self._log(logging.DEBUG, f"Cookie kaydetme hatası: {e}")
    def _load_cookies(self):
        """B2: Kaydedilmiş cookie'leri yükle ve oturumun hÃƒÂ¢lÃƒÂ¢ geçerli olup olmadığını kontrol et"""
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
            
            self._log(logging.INFO, f"🔍 {len(cookies)} cookie yüklendi, oturum kontrol ediliyor...")
            
            # Appointment sayfasına giderek oturum geçerliliğini test et
            self.driver.get(self.APPOINTMENT_URL)
            time.sleep(3)
            
            current_url = self.driver.current_url.lower()
            if 'login' not in current_url and 'account' not in current_url:
                self._log(logging.INFO, "✅ Cookie oturumu geçerli! Login atlanıyor.")
                self.is_logged_in = True
                return True
            else:
                self._log(logging.INFO, "❌ Cookie oturumu süresi dolmuş. Normal login yapılacak.")
                return False
        except Exception as e:
            self._log(logging.DEBUG, f"Cookie yükleme hatası: {e}")
            return False
    def login(self, email: str, password: str, solve_captcha: bool = True) -> bool:
        """BLS sitesine giriş yap Ã¢â‚¬â€ state-machine yaklaşımı"""
        try:
            self._log(logging.INFO, "Giriş yapılıyor...")
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
            # Ã¢â€â‚¬Ã¢â€â‚¬ Adım 1: Email alanını bul ve doldur Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            visible_inputs = self._get_visible_text_inputs()
            self._log(logging.INFO, f"Görünür input sayısı: {len(visible_inputs)}")
            if not visible_inputs:
                self._log(logging.ERROR, "Hiç görünür input bulunamadı")
                return False
            email_field = visible_inputs[0]
            self.driver.execute_script(
                "arguments[0].value = arguments[1]; "
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                email_field, email
            )
            self._log(logging.INFO, f"Email girildi: {email}")
            time.sleep(0.5)
            # Ã¢â€â‚¬Ã¢â€â‚¬ Adım 2: Verify butonu Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            verify_btn = self._find_visible_button(["Verify", "verify", "VERIFY"])
            if verify_btn:
                self._log(logging.INFO, "Verify tıklanıyor...")
                self.driver.execute_script("arguments[0].click();", verify_btn)
                time.sleep(3)
                # Erken başarı kontrolü Ã¢â‚¬â€ kullanıcı zaten giriş yaptı mı?
                if self._check_login_success():
                    return True
                # CAPTCHA var mı?
                if solve_captcha:
                    api_key = self.config.get("2captcha_key", "").strip()
                    captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                    if captcha_solver.is_captcha_present():
                        # KRİTİK KONTROL: Ã…ifre alanı zaten var mı?
                        # Eğer şifre alanı varsa, önce şifre girilmeli. Captcha'yı burda çözme!
                        if self._find_password_field():
                            self._log(logging.INFO, "Captcha ve Ã…ifre alanı aynı anda tespit edildi. Önce şifre girilecek...")
                        else:
                            self._log(logging.INFO, "Sadece CAPTCHA var (Email onayı olabilir). Çözülüyor...")
                            captcha_solver.solve()
                            # CAPTCHA sonrası giriş başarılı mı?
                            if self._check_login_success():
                                return True
                total_wait = 60 if not solve_captcha else 20
                self._log(logging.INFO, f"Ã…ifre sayfası bekleniyor... ({total_wait/2} sn)")
                for _ in range(total_wait):
                    time.sleep(0.5)
                    if self._check_login_success():
                        return True
                    inputs = self._get_visible_text_inputs()
                    if inputs:
                        break
                    # Eğer Captcha varsa ve çözülmediyse hala bekliyor olabiliriz
                    if solve_captcha and captcha_solver.is_captcha_present() and not self._find_password_field():
                         self._log(logging.DEBUG, "Hala Captcha var ama şifre yok...")
                         
                    self._log(logging.DEBUG, "Ã…ifre alanı henüz yok, bekleniyor...")
            # Ã¢â€â‚¬Ã¢â€â‚¬ Adım 3: Ã…ifre alanı ve CAPTCHA Döngüsü Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            # CAPTCHA yanlış girildiğinde sayfa yenilenebilir veya şifre silinebilir.
            # Bu yüzden şifre girme ve CAPTCHA çözme işlemini bir döngüde yapıyoruz.
            max_login_attempts = 3
            
            for attempt in range(max_login_attempts):
                self._log(logging.INFO, f"Giriş/Ã…ifre Denemesi: {attempt + 1}/{max_login_attempts}")
                
                # Sayfanın yüklenmesi/yenilenmesi için kısa bir bekleme
                time.sleep(1)
                
                if self._check_login_success():
                    return True
                    
                password_field = self._find_password_field()
                if not password_field:
                    if attempt == 0:
                        # İlk denemede bulamazsa, belki kullanıcıya manuel giriş için süre tanınmalı
                        self._log(logging.WARNING, "Ã…ifre alanı bulunamadı Ã¢â‚¬â€ lütfen tarayıcıdan şifreyi girin (90 sn)")
                        for _ in range(90):
                            time.sleep(1)
                            if self._check_login_success():
                                self._log(logging.INFO, "✅ Kullanıcı manuel giriş yaptı!")
                                self.is_logged_in = True
                                return True
                            password_field = self._find_password_field()
                            # Eğer şifre alanı geri geldiyse, döngüden çıkıp JS ile dolduracak
                            if password_field: 
                                break
                    
                    if not password_field:
                        self._log(logging.ERROR, "Ã…ifre alanı bulunamadı, yeniden deneniyor...")
                        continue # Bir sonraki denemeye geç (belki sayfa yenileniyordur)
                # Ã…ifre alanı varsa doldur
                if password_field:
                    try:
                        self.driver.execute_script(
                            "arguments[0].value = arguments[1]; "
                            "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                            password_field, password
                        )
                        self._log(logging.INFO, "Ã…ifre girildi")
                        time.sleep(0.5)
                    except Exception as pe:
                        self._log(logging.ERROR, f"Ã…ifre alanına yazılırken hata: {pe}")
                        continue # Hata olursa tekrar dene
                if not solve_captcha:
                    self._log(logging.WARNING, "CAPTCHA ve Ã…ifre (Varsa) Manuel Giriş için 20 sn bekleniyor...")
                    time.sleep(20)
                    if self._check_login_success(): return True
                    continue
                # Ã¢â€â‚¬Ã¢â€â‚¬ CAPTCHA (Ã…ifre girildikten sonra) Ã¢â€â‚¬Ã¢â€â‚¬
                api_key = self.config.get("2captcha_key", "").strip()
                captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                
                if captcha_solver.is_captcha_present():
                     self._log(logging.INFO, "CAPTCHA çözülüyor (Ã…ifre girildi, şimdi Submit edilecek)...")
                     if captcha_solver.solve():
                         self._log(logging.INFO, "Captcha Submit edildi. Sonuç bekleniyor...")
                         time.sleep(5)
                         if self._check_login_success(log=True):
                             return True
                         else:
                             self._log(logging.WARNING, "Captcha çözüldü ancak giriş başarılı olmadı. Ã…ifre silinmiş olabilir, tekrar denenecek.")
                             # Bazen captcha submit sonrası başarısız olursa error message çıkar
                             self._check_login_success(log=False) # Hataları loglamak için
                     else:
                         self._log(logging.ERROR, "Captcha çözülemedi, tekrar denenecek.")
                else:
                    # Captcha yoksa ama şifre girildiyse, belki bir Login butonu vardır
                    login_btn = self._find_visible_button(["Login", "Giriş"])
                    if login_btn:
                        self._log(logging.INFO, "CAPTCHA yok, Login butonuna tıklanıyor...")
                        self.driver.execute_script("arguments[0].click();", login_btn)
                        time.sleep(4)
                        if self._check_login_success(log=True):
                            return True
                    else:
                        self._log(logging.INFO, "CAPTCHA yok, Login butonu da yok. Sayfanın yüklenmesi bekleniyor...")
                        time.sleep(2)
                        if self._check_login_success(log=True): return True
            # Döngü bitti ve girilemedi
            return self._check_login_success(log=True)
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            self._log(logging.ERROR, f"Giriş hatası: {e}")
            self._log(logging.ERROR, f"Traceback:\n{tb_str}")
            self.is_logged_in = False
            return False
    def _check_login_success(self, log: bool = False) -> bool:
        """URL'ye bakarak giriş başarılı mı kontrol et"""
        try:
            url = self.driver.current_url.lower()
            if "login" not in url:
                self.is_logged_in = True
                if log:
                    self._log(logging.INFO, f"✅ Giriş başarılı! URL: {url}")
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
                                self._log(logging.ERROR, f"Hata: {e.text.strip()}")
                    except Exception:
                        pass
                self._log(logging.ERROR, f"Giriş başarısız Ã¢â‚¬â€ URL: {url}")
                METRIC_LOGIN_ATTEMPTS.labels('fail').inc()
                proxy_manager.report_failure(self.proxy) # Report failure on login error
                report_account_risk(self.user_data.get('id'), 15, reason="Giriş Hatası (Login Fail)")
        except Exception:
            pass
        return False
    def _find_password_field(self):
        """Ã…ifre alanını bul Ã¢â‚¬â€ type='password' veya görünür ikinci input"""
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
            self._log(logging.WARNING, "CAPTCHA otomatik çözülemedi Ã¢â‚¬â€ lütfen tarayıcıdan manuel çözün (15 sn)")
            for _ in range(15):
                time.sleep(1)
                if not captcha_solver.is_captcha_present():
                    self._log(logging.INFO, "CAPTCHA manuel olarak çözüldü")
                    break
    def register(self, email: str, password: str, first_name: str, last_name: str,
                 phone: str) -> bool:
        """Yeni hesap oluştur"""
        try:
            self._log(logging.INFO, "Kayıt sayfasına gidiliyor...")
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
                    self._log(logging.WARNING, f"Alan bulunamadı: {field_id} - {e}")
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
                        self._log(logging.INFO, "Sisteme Giriş Yap butonu tıklandı.")
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
                self._log(logging.INFO, "Kayıt başarılı!")
                return True
            else:
                try:
                    error = self.driver.find_element(By.CLASS_NAME, "validation-summary-errors")
                    self._log(logging.ERROR, f"Kayıt hatası: {error.text}")
                except NoSuchElementException:
                    self._log(logging.WARNING, "Kayıt durumu belirsiz")
                return False
        except Exception as e:
            self._log(logging.ERROR, f"Kayıt hatası: {e}")
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
            self._log(logging.INFO, "Randevu sayfasına gidiliyor...")
            
            # Ã¢â€â‚¬Ã¢â€â‚¬ HTTP Pre-Check: Site Ayakta Mı? Ã¢â€â‚¬Ã¢â€â‚¬
            import requests
            try:
                proxies = None
                if getattr(self, "proxy", None):
                    proxies = {"http": f"http://{self.proxy}", "https": f"http://{self.proxy}"}
                import urllib3
                urllib3.disable_warnings() # Gizler InsecureRequestWarning
                r = requests.head(self.APPOINTMENT_URL, timeout=10, proxies=proxies, verify=False)
                if r.status_code >= 500:
                    self._log(logging.WARNING, f"HTTP Pre-check başarısız (Status {r.status_code}) Ã¢â‚¬â€ site çökmüş olabilir.")
                    result['message'] = f"Site down (HTTP {r.status_code})"
                    return result
            except requests.exceptions.Timeout:
                self._log(logging.WARNING, "HTTP Pre-check başarısız (Timeout). Site Offline.")
                result['message'] = "Site timeout (HTTP Pre-check)"
                return result
            except Exception as e:
                self._log(logging.WARNING, f"HTTP Pre-check Exception: {str(e)[:50]}")
                # Hata durumunda devam et, proxy kaynaklı basit bir ssl hatası olabilir.
            self.driver.get(self.APPOINTMENT_URL)
            time.sleep(3)
            self._check_and_solve_captcha()  # Sürpriz CAPTCHA kontrolü
            # Risk-based Delay for appointment page
            risk_score = get_account_risk(self.user_data.get('id'))
            time.sleep(3 if risk_score > 40 else 1)
            # ... Cloudflare veya "Access Denied" sayfa kontrolü eklenebilir
            if "Access Denied" in self.driver.page_source or "403 Forbidden" in self.driver.title:
                 self._log(logging.ERROR, "403 Forbidden Access!")
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
            # Ã¢â€â‚¬Ã¢â€â‚¬ Email onayı bekleniyor mu? Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if "dataprotection" in current_url or "emailsent" in current_url:
                result['message'] = (
                    "⚠️ Email onayı gerekiyor! "
                    "Kayıtlı email adresinize gelen linke tıklayın, "
                    "ardından bot otomatik devam edecek."
                )
                self._log(logging.WARNING, "Email onayı bekleniyor Ã¢â‚¬â€ lütfen email'inizdeki linke tıklayın!")
                # Kullanıcı email'i onaylayana kadar bekle (max 5 dk)
                for _ in range(60):
                    time.sleep(5)
                    url = self.driver.current_url.lower()
                    if "dataprotection" not in url and "emailsent" not in url:
                        self._log(logging.INFO, "Email onaylandı, devam ediliyor...")
                        # Tekrar randevu sayfasına git
                        self.driver.get(self.APPOINTMENT_URL)
                        time.sleep(3)
                        self._check_and_solve_captcha()
                        break
                else:
                    result['message'] = "Email onayı 5 dakika içinde yapılmadı"
                    return result
            # Ã¢â€â‚¬Ã¢â€â‚¬ Ã…artlar/Onay sayfası var mı? Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            if any(kw in page_text.lower() for kw in [
                "accept", "agree", "terms", "şartlar", "kabul", "consent"
            ]):
                # "Go To Home" veya "Accept" butonuna tıkla
                accept_btn = self._find_visible_button([
                    "Accept", "Agree", "Kabul", "I Accept", "Go To Home", "Continue"
                ])
                if accept_btn:
                    self._log(logging.INFO, f"Onay sayfası Ã¢â‚¬â€ '{accept_btn.text.strip()}' tıklanıyor...")
                    self.driver.execute_script("arguments[0].click();", accept_btn)
                    time.sleep(2)
                    # Tekrar randevu sayfasına git
                    self.driver.get(self.APPOINTMENT_URL)
                    time.sleep(3)
            # Ã¢â€â‚¬Ã¢â€â‚¬ Randevu sayfası içeriğini logla (debug) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            try:
                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                self._log(logging.INFO, f"Randevu sayfası URL: {self.driver.current_url}")
                self._log(logging.DEBUG, f"Sayfa içeriği (ilk 500): {page_text[:500]}")
            except Exception:
                page_text = ""
            # Ã¢â€â‚¬Ã¢â€â‚¬ Başvuru profil formu doldurulmamış mı? Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            # "You have not filled out and completed the applicant detail form"
            if "have not filled out" in page_text.lower() or "complete the form" in page_text.lower():
                self._log(logging.WARNING, 
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
                self._log(logging.WARNING, 
                    "ÄŸÅ¸â€œâ€¹ Lütfen tarayıcıda açılan formu doldurun:\n"
                    "   Ã¢â‚¬Â¢ Surname At Birth, Place Of Birth\n"
                    "   Ã¢â‚¬Â¢ Country Of Birth, Current Nationality\n"
                    "   Ã¢â‚¬Â¢ Gender, Marital Status\n"
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
                        self._log(logging.INFO, "✅ Profil formu tamamlandı, randevu kontrolüne devam ediliyor...")
                        # Tekrar randevu sayfasına git
                        self.driver.get(self.APPOINTMENT_URL)
                        time.sleep(3)
                        break
                else:
                    result['message'] = "Profil formu 10 dakika içinde tamamlanmadı"
                    return result
            # Ã¢â€â‚¬Ã¢â€â‚¬ Randevu formunu doldur Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            categories_to_check = [c.strip() for c in self.user_data.get("category", "").split(",") if c.strip()]
            if not categories_to_check:
                self._log(logging.WARNING, "Kategori girilmemiş.")
                result['message'] = "Kategori eksik"
                return result
            all_available_results = []
            
            for index, cat in enumerate(categories_to_check):
                self._log(logging.INFO, f"--- Kategori Kontrol Ediliyor: {cat} ---")
                
                if index > 0:
                    # Yeni kategori kontrolü öncesi, normal taramaya benzer bekleme koy
                    delay = int(self.user_data.get("check_interval", 60))
                    self._log(logging.INFO, f"Sıradaki kategori ({cat}) için {delay} saniye bekleniyor...")
                    time.sleep(delay)
                    
                    self.driver.get(self.APPOINTMENT_URL)
                    time.sleep(3)
                    self._check_and_solve_captcha()
                form_filled = self._fill_appointment_form(cat)
                
                # Bazen Submit ettikten sonra "Pending Appointment" hatası verir
                if self._handle_pending_appointment():
                    self._log(logging.INFO, f"  ÄŸÅ¸â€â€ Pending silindiği için form {cat} için tekrar dolduruluyor...")
                    form_filled = self._fill_appointment_form(cat)
                if not form_filled:
                    if index == 0:
                        result['message'] = "Randevu formu doldurulamadı (Ayarları kontrol edin)"
                        self._log(logging.WARNING, "Randevu formu eksik Ã¢â‚¬â€ ayarlar kontrol edilmeli")
                    continue
                # Ã¢â€â‚¬Ã¢â€â‚¬ Post-form CAPTCHA kontrolü Ã¢â€â‚¬Ã¢â€â‚¬
                captcha_solved = False
                try:
                    self._log(logging.INFO, "  Form sonrası CAPTCHA kontrolü yapılıyor...")
                    for captcha_retry in range(3):
                        solved_attempt = self._check_and_solve_captcha()
                        if not solved_attempt:
                            self._log(logging.WARNING, "  CAPTCHA çözme metodunda hata oluştu (atlandı).")
                            break
                        
                        time.sleep(3)  # Sayfanın yenilenmesi veya yönlenmesi için bekle
                        
                        # Artık takvim sayfasına (apptsloti) başarıyla geçtik mi? Veya Captcha yok oldu mu?
                        from bot.captcha_solver import CaptchaSolver
                        checker = CaptchaSolver(self.driver, api_key=self.config.get("2captcha_key", "").strip())
                        
                        if "apptsloti" in self.driver.current_url.lower():
                            self._log(logging.INFO, "  ✅ Takvim sayfasına başarıyla geçildi.")
                            captcha_solved = True
                            break
                            
                        if not checker.is_captcha_present():
                            self._log(logging.INFO, "  ✅ Ekranda CAPTCHA kalmadı, takvim aranıyor...")
                            captcha_solved = True
                            break
                            
                        self._log(logging.WARNING, f"  ⚠️ CAPTCHA ekranda kalmaya devam ediyor! Yanlış çözüldü veya sayfa yenilendi. Tekrar deneniyor... ({captcha_retry+1}/3)")
                    
                except Exception as ce:
                    self._log(logging.WARNING, f"Post-submit CAPTCHA hatası: {ce}")
                
                # Müsait tarih kontrolü
                available_dates = self._find_available_dates()
                if available_dates:
                    self._log(logging.INFO, f"RANDEVU BULUNDU ({cat}): {available_dates}")
                    for d in available_dates:
                        all_available_results.append({"category": cat, "day": d})
                else:
                    self._log(logging.INFO, f"Müsait randevu bulunamadı ({cat})")
            if all_available_results:
                result['available'] = True
                
                # En erken tarihe göre sırala
                all_available_results.sort(key=lambda x: int(x['day']) if str(x['day']).isdigit() else 999)
                
                # Sadece loglama ve bildirim için görsel tarihleri hazÃƒâ€Ã‚Â±rla
                formatted_dates = [f"{r['day']} ({r['category']})" for r in all_available_results]
                result['dates'] = formatted_dates
                
                # Auto book için dict listesini de döndürelim
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

    def _check_and_solve_captcha(self) -> bool:
        """
        CAPTCHA sayfasinda miyiz kontrol et, oyleyse coz.
        (Turkce karakter bilerek kullanilmamistir, encoding hatalarina karsi koruma amacli)
        """
        from bot.captcha_solver import CaptchaSolver
        solver = CaptchaSolver(self.driver, api_key=self.config.get("2captcha_key", "").strip())
        if solver.is_captcha_present():
            self._log(logging.INFO, "CAPTCHA algilandi, cozum baslatiliyor...")
            return solver.solve()
        
        return True

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
            # Ã¢â€â‚¬Ã¢â€â‚¬ 1. Helper: Select2 Seçimi (Updated Logic) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
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
                                logger.info(f"  Ã¢Å“â€œ {step_name}: {o.text} (Tam)")
                                time.sleep(0.2)
                                return True
                        # Kısmi
                        for o in visible:
                            if search_norm in normalize_tr(o.text):
                                self.driver.execute_script("arguments[0].click();", o)
                                logger.info(f"  Ã¢Å“â€œ {step_name}: {o.text} (Kısmi)")
                                time.sleep(0.2)
                                return True
                    # B. Generic Li Fallback
                    logger.info(f"  [DEBUG] {step_name}: Standart yok, genel LI aranıyor...")
                    all_lis = self.driver.find_elements(By.TAG_NAME, "li")
                    vis_lis = [li for li in all_lis if li.is_displayed() and li.text.strip()]
                    for li in vis_lis:
                        if search_norm in normalize_tr(li.text):
                            self.driver.execute_script("arguments[0].click();", li)
                            logger.info(f"  Ã¢Å“â€œ {step_name}: {li.text} (Fallback)")
                            time.sleep(0.2)
                            return True
                    
                    logger.warning(f"  {step_name}: '{search_value}' bulunamadı.")
                    return False
                except Exception as e:
                    logger.debug(f"  select2_pick error: {e}")
                return False
            # Ã¢â€â‚¬Ã¢â€â‚¬ 2. Helper: Label Bazlı Tıklama (Hibrit: DOM + Koordinat) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
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
                    # Ã¢â€â‚¬Ã¢â€â‚¬ STRATEJI A: Global Koordinat (Genişletilmiş) Ã¢â€â‚¬Ã¢â€â‚¬
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
                    # Ã¢â€â‚¬Ã¢â€â‚¬ STRATEJI B: JS Element From Point (Viewport Coord) Ã¢â€â‚¬Ã¢â€â‚¬
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
            # Ã¢â€â‚¬Ã¢â€â‚¬ 3. Step-by-Step Filling (with retry on label-not-found) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            
            max_form_retries = 3
            for form_attempt in range(max_form_retries):
                if form_attempt > 0:
                    logger.warning(f"  ÄŸÅ¸â€â€ Form doldurma tekrar deneniyor... (Deneme {form_attempt + 1}/{max_form_retries})")
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
                            logger.info(f"  Ã¢Å“â€œ Appointment For: {appointment_for}")
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
            logger.info("ÄŸÅ¸â€™ Premium Popup tespit edildi! Onaylanıyor...")
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
                            logger.info(f"ÄŸÅ¸â€™ Premium Onay butonu tıklandı: {txt}")
                            self.driver.execute_script("arguments[0].click();", btn)
                            time.sleep(1.5)
                            return
                except: pass
            
            logger.warning("ÄŸÅ¸â€™ Premium Popup var ama 'Accept' butonu bulunamadı!")
            
        except Exception as e:
            logger.error(f"  [DEBUG] Handle Premium Popup Hatası: {e}")
            return False
            
    def _find_available_dates(self) -> list:
        """
        Takvim (DatePicker) menüsünde arka plan rengi yeşil olan, 
        yani müsait olan günlerin numaralarını liste olarak döndürür.
        """
        available_days = []
        try:
            self._log(logging.INFO, "Takvimde yeşil (müsait) günler aranıyor...")
            
            # Eğer takvim açık değilse açmayı dene
            calendar = self._find_element_multi([
                (By.CSS_SELECTOR, "div.k-calendar-container")
            ], timeout=1)
            
            if not calendar or not calendar.is_displayed():
                self._log(logging.INFO, "  [NATIVE JS] Görünür takvim ikonu aranıyor ve tıklanıyor...")
                clicked = self.driver.execute_script("""
                    var checkVis = function(el) {
                        var rect = el.getBoundingClientRect();
                        if(rect.width === 0 || rect.height === 0) return false;
                        var style = window.getComputedStyle(el);
                        if(style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
                        var p = el.parentElement;
                        while(p && p !== document.body) {
                            var ps = window.getComputedStyle(p);
                            if(ps.display === 'none' || ps.visibility === 'hidden' || parseFloat(ps.opacity) === 0) return false;
                            p = p.parentElement;
                        }
                        return true;
                    };
                    var icon = Array.from(document.querySelectorAll('span.k-icon.k-i-calendar, .k-datepicker .k-select')).find(checkVis);
                    if (icon) { icon.click(); return true; }
                    return false;
                """)
                
                # Bekle ve Takvimin acildigini dogrula
                try:
                    from selenium.webdriver.support.ui import WebDriverWait
                    from selenium.webdriver.support import expected_conditions as EC
                    WebDriverWait(self.driver, 3).until(
                        EC.visibility_of_element_located((By.CSS_SELECTOR, "div.k-calendar-container"))
                    )
                except:
                    self._log(logging.WARNING, "Takvim animasyonu uzun sürdü veya takvim UI'ı saptanamadı.")
                time.sleep(0.5)
            
            # 6 Ay boyunca ileri giderek tara (Randevu bulursa durur)
            for month_offset in range(6):
                # Mevcut ayın adını al
                month_title = ""
                try:
                    title_el = self.driver.find_element(By.CSS_SELECTOR, ".k-nav-fast")
                    if title_el.is_displayed():
                        month_title = title_el.text.strip()
                except: pass

                if month_title:
                    self._log(logging.INFO, f"  Taranan Ay: {month_title}")
                else:
                    self._log(logging.INFO, f"  Taranan Ay: Mevcut + {month_offset}")

                # JS Honeypot Bypass: Gorunur yesil hucreleri topla
                js_find_green_dates = """
                return (function() {
                    function isVisible(el) {
                        var rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) return false;
                        if (rect.right < 0 || rect.bottom < 0 ||
                            rect.left > (window.innerWidth || document.documentElement.clientWidth) ||
                            rect.top > (window.innerHeight || document.documentElement.clientHeight)) return false;
                        var curr = el;
                        while (curr) {
                            var style = window.getComputedStyle(curr);
                            if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
                            curr = curr.parentElement;
                        }
                        return true;
                    }
                    var green_days = [];
                    var cells = document.querySelectorAll("td[role='gridcell']:not(.k-other-month):not(.k-state-disabled) .k-link");
                    for (var i = 0; i < cells.length; i++) {
                        var link = cells[i];
                        if (isVisible(link)) {
                            var bg = window.getComputedStyle(link).backgroundColor;
                            var rgba = bg.match(/[0-9]+/g);
                            if (rgba && rgba.length >= 3) {
                                var r = parseInt(rgba[0]), g = parseInt(rgba[1]), b = parseInt(rgba[2]);
                                if (g > 100 && g > r * 2) {
                                    green_days.push(link.textContent.trim());
                                }
                            }
                        }
                    }
                    return green_days;
                })();
                """
                green_days = self.driver.execute_script(js_find_green_dates)
                
                if green_days:
                    for day_text in green_days:
                        if day_text.isdigit():
                            if month_title:
                                full_date = f"{day_text} ({month_title})"
                            else:
                                full_date = day_text
                            available_days.append(full_date)
                
                # Eğer bu ayda randevu bulduysak, sonrakilere bakmaya gerek yok.
                if available_days:
                    break
                
                # Sonraki aya gec
                if month_offset < 5:
                    # Honeypot bypass: Sadece gercekten boyutlari olan tusa bas
                    next_clicked = self.driver.execute_script("""
                        var checkVis = function(el) {
                            var rect = el.getBoundingClientRect();
                            if(rect.width === 0 || rect.height === 0) return false;
                            var style = window.getComputedStyle(el);
                            if(style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
                            var p = el.parentElement;
                            while(p && p !== document.body) {
                                var ps = window.getComputedStyle(p);
                                if(ps.display === 'none' || ps.visibility === 'hidden' || parseFloat(ps.opacity) === 0) return false;
                                p = p.parentElement;
                            }
                            return true;
                        };
                        var vis = Array.from(document.querySelectorAll('.k-nav-next, .k-calendar .k-header .k-next-view')).find(checkVis);
                        if(vis) { vis.click(); return true; }
                        return false;
                    """)
                    if next_clicked:
                        self._log(logging.INFO, "  Sonraki aya geçiliyor...")
                        time.sleep(1.0) # Ajax animasyonunu bekle
                    else:
                        self._log(logging.DEBUG, "Sonraki ay butonu bulunamadı (veya max limit)")
                        break # Buton yoksa donguden cik

            if available_days:
                self._log(logging.INFO, f"Bulunan müsait günler: {available_days}")
                
        except Exception as e:
            self._log(logging.ERROR, f"Takvim okuma hatası: {e}")
            
        # Eğer formatlı stringler varsa set() sort() desteklemez, normal dondur
        return list(set(available_days))

    def _handle_pending_appointment(self) -> bool:
        """
        Pending Appointment ekranı gelirse (Adayın zaten bekleyen randevusu var),
        'Book New Appointment'ı seçip devam eder.
        """
        try:
            logger.info("  [DEBUG] Bekleyen randevu (Pending Appointment) kontrol ediliyor...")
            
            # 1. Adım: 'Book New Appointment' radio butonunu bul ve tıkla
            # ID'si değişebilir diye label/value/text hepsi üzerinden şansımızı deniyoruz.
            try:
                # Standart id genelde 'rbNewAppointment'
                rb_new = self._find_element_multi([
                    (By.ID, "rbNewAppointment"),
                    (By.CSS_SELECTOR, "input[type='radio'][value='NewAppointment']"),
                    (By.XPATH, "//input[@type='radio' and contains(following-sibling::text(), 'Book New Appointment')]")
                ], timeout=3)
                if rb_new:
                    self.driver.execute_script("arguments[0].click();", rb_new)
                    logger.info("  'Book New Appointment' seçeneği işaretlendi.")
                    time.sleep(1)
            except Exception as e:
                logger.warning(f"  [DEBUG] 'Book New Appointment' radio butonu bulunamadı veya tıklanamadı: {e}")
                pass # Belki tek seçenektir veya direk submit edilebilir

            # 2. Adım: Submit/Proceed butonuna tıkla
            try:
                # Standart id 'btnSubmit'
                btn_submit = self._find_element_multi([
                    (By.ID, "btnSubmit"),
                    (By.CSS_SELECTOR, "input[type='submit'][value='Submit']"),
                    (By.XPATH, "//input[@type='submit' and contains(@value, 'Submit')]")
                ], timeout=3)
                
                if btn_submit:
                    # Kendo UI bazen normal click'i engeller, JS ile tıkla:
                    self.driver.execute_script("arguments[0].click();", btn_submit)
                    logger.info("  Pending Appointment sayfasında Submit'e basıldı.")
                    time.sleep(3) # Yönlendirme için bekle
                    return True
                else:
                    logger.warning("  [DEBUG] Pending Appointment sayfasında Submit butonu bulunamadı.")
            except Exception as e:
                logger.warning(f"  [DEBUG] Pending Appointment Submit hatası: {e}")
                
            return False

        except Exception as e:
            logger.error(f"Bekleyen randevu işleme (Pending Appointment) genel hatası: {e}")
            return False

    def book_appointment(self, target_slot: dict = None, appointment_details: dict = None) -> bool:
        """Kritik Randevu Alma Ana Fonksiyonu (Düzeltildi)"""
        try:
            # Geriye dönük uyumluluk için hem target_slot hem appointment_details kabul ediliyor
            details = target_slot or appointment_details or {}
            category_id = details.get("category_id") or self.user_data.get("category", "")
            category_name = details.get("category_name") or details.get("category", "Bilinmeyen Kategori")
            target_date = details.get("date") or details.get("day")  # "DD/MM/YYYY" veya "6 (May 2026)"
            
            day_num = ""
            if target_date:
                t_str = str(target_date).strip()
                if '/' in t_str:
                    day_num = t_str.split('/')[0]
                elif ' ' in t_str: 
                    day_num = t_str.split(' ')[0] # "6" from "6 (May 2026)"
                elif hasattr(target_date, 'day'):
                    day_num = str(target_date.day)
                else:
                    day_num = t_str # Sadece numara geldiyse (örn: '4')

            # '05' formatındaysa '5' yapmak için
            day_num = day_num.lstrip('0')
            
            self._log(logging.INFO, f"🚀 Kategori [{category_name}] - Tarih [{target_date}] için form dolduruluyor...")

            current_url = self.driver.current_url.lower()
            if "apptsloti" not in current_url and "slotselection" not in current_url:
                appointment_url = f"https://turkey.blsspainglobal.com/Global/bls/apptsloti/{category_id}"
                self.driver.get(appointment_url)
                time.sleep(2)
            else:
                self._log(logging.INFO, "  Zaten slot seçim sayfasındayız, URL reload atlanıyor.")
                
            current_url = self.driver.current_url

            
            # --- 1) Pending Appointment Kontrolü ---
            if "PendingAppointment" in current_url:
                self._log(logging.WARNING, "⚠️ Pending Appointment (Bekleyen Randevu) ekranıyla karşılaşıldı!")
                handle_success = self._handle_pending_appointment()
                if not handle_success:
                    self._log(logging.ERROR, "❌ Bekleyen randevu ekranı aşılamadı.")
                    return False
                
                # Başarılı geçildiyse sayfa değişmiştir, current_url'yi güncelle
                current_url = self.driver.current_url

            # --- 2) Zaten Applicant Selection Sayfasındaysa ---
            if "ApplicantSelection" in current_url:
                self._log(logging.INFO, "  Doğrudan Applicant Selection sayfasındayız. Takvim adımı atlanıyor.")
                date_clicked = True
                slot_selected = True
                
            else:
                # --- 3) Normal Takvim/Slot Seçimi ---
                self._log(logging.INFO, "  Takvim/Slot seçimi adımındayız...")
            
                target_month = ""
                if "/" in str(target_date):
                    parts = str(target_date).split('/')
                    if len(parts) >= 2:
                        mt = int(parts[1])
                        month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                        if 1 <= mt <= 12:
                            target_month = month_names[mt]
                if "(" in str(target_date):
                    target_month = str(target_date).split("(")[1].replace(")", "").strip()
                
                self._log(logging.INFO, f"  Kendo takvimde gun seciliyor: day_num='{day_num}' | target_date='{target_date}' | target_month='{target_month or 'BOŞ - navigasyon YOK'}'")
                
                # --- [DETERMINISTIC CALENDAR INTERACTION - Tüm hatalar çözüldü] ---
                # ROOT CAUSE FIX #1: Regex'i /[0-9]+/g kullan. Python triple-string'de \d
                #   Python->JS escape'den geçince /\\d+/g olur (literal backslash+d, rakam değil)!
                #   Bu yüzden rgba her zaman null, yeşil renk hiç tespit edilemiyordu.
                # ROOT CAUSE FIX #2: Viewport sınırı kontrolü (left:-9999px honeypot'u yakalar)
                # ROOT CAUSE FIX #3: Tek atomik JS fonksiyon (Python<->JS round-trip race yok)
                # ROOT CAUSE FIX #4: $(link).trigger('click') - Kendo'nun kendi event handler'ı
                try:
                    # ─── ADIM 1: TAKVİMİ AÇ ───────────────────────────────────────────
                    self._log(logging.INFO, "  [CAL] Adim 1/3: Takvim ikonu tiklaniyor...")
                    
                    # Takvim zaten açık mı kontrol et
                    cal_open = self.driver.execute_script("""
                        var cal = document.querySelector('div.k-calendar-container, div.k-animation-container');
                        return cal && window.getComputedStyle(cal).display !== 'none';
                    """)
                    
                    if not cal_open:
                        # Görünür takvim ikonunu bul ve tıkla
                        # Takvimin gerçekten acildigini garanti altina almak icin retry mekanizmasi
                        cal_opened_successfully = False
                        for cal_try in range(3):
                            opened = self.driver.execute_script("""
                                function isReal(el) {
                                    if (!el) return false;
                                    var r = el.getBoundingClientRect();
                                    if (r.width <= 0 || r.height <= 0) return false;
                                    if (r.right < -50) return false;
                                    var c = el;
                                    while (c) {
                                        var s = window.getComputedStyle(c);
                                        if (s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;
                                        c = c.parentElement;
                                    }
                                    return true;
                                }
                                
                                // 1) Takvim zaten acik mi?
                                var openCals = Array.from(document.querySelectorAll('.k-animation-container .k-calendar-container, .k-calendar-container.k-state-border-down'))
                                    .filter(isReal);
                                if (openCals.length > 0) return 'ALREADY_OPEN';
                                
                                // 2) Acik degilse butonu bul ve tikla
                                var icon = Array.from(document.querySelectorAll(
                                    'span.k-icon.k-i-calendar, .k-datepicker .k-select, .k-picker-wrap .k-select'
                                )).find(isReal);
                                
                                if (icon) {
                                    icon.scrollIntoView({block: 'center', behavior: 'instant'});
                                    if (typeof $ !== 'undefined') { $(icon).trigger('click'); }
                                    else { icon.click(); }
                                    return 'CLICKED_ICON';
                                }
                                
                                // Fallback: input'a tikla
                                var input = Array.from(document.querySelectorAll('input[data-role="datepicker"]')).find(isReal);
                                if (input) {
                                    input.scrollIntoView({block: 'center', behavior: 'instant'});
                                    if (typeof $ !== 'undefined') { $(input).trigger('click'); }
                                    else { input.click(); }
                                    return 'CLICKED_INPUT';
                                }
                                
                                return 'NOT_FOUND';
                            """)
                            
                            if opened == 'NOT_FOUND':
                                self._log(logging.ERROR, "  [CAL] Takvim ikonu veya input'u bulunamadi!")
                                break
                            
                            if opened == 'ALREADY_OPEN':
                                cal_opened_successfully = True
                                break
                            
                            # Tikladik, simdi gercekten acilmasini bekle
                            try:
                                WebDriverWait(self.driver, 3).until(
                                    lambda d: d.execute_script("""
                                        var cals = document.querySelectorAll('.k-animation-container .k-calendar-container, .k-calendar-container.k-state-border-down');
                                        for(var i=0; i<cals.length; i++){
                                            var style = window.getComputedStyle(cals[i].parentElement || cals[i]);
                                            if (style.display !== 'none' && parseFloat(style.opacity) > 0) return true;
                                        }
                                        return false;
                                    """)
                                )
                                cal_opened_successfully = True
                                break # Basariyla acildi
                            except Exception:
                                self._log(logging.WARNING, f"  [CAL] Takvim acilisi gozlemlenemedi (deneme {cal_try+1}/3), tekrar tiklaniyor...")
                                time.sleep(0.5)
                        
                        if not cal_opened_successfully:
                           self._log(logging.ERROR, "  [CAL] Takvim pop-up'i acilamadi, islem iptal.")
                           return False
                        
                        # Takvimi viewport'a getir (sayfa altında açılmış olabilir!)
                        self.driver.execute_script("""
                            var cal = document.querySelector('.k-animation-container .k-calendar-container, .k-calendar-container');
                            if (cal) cal.scrollIntoView({block: 'center', behavior: 'instant'});
                        """)
                        time.sleep(1.0) # Settle suresi
                    
                    # ─── ADIM 2: DOĞRU AYA GİT ────────────────────────────────────────
                    if target_month:
                        self._log(logging.INFO, f"  [CAL] Adim 2/3: '{target_month}' ayina gidiliyor...")
                        for nav_attempt in range(18):  # Max 18 ay ileri
                            # Görünür takvim başlığını oku
                            title_text = self.driver.execute_script("""
                                var activeCal = document.querySelector('.k-animation-container .k-calendar-container, .k-calendar-container.k-state-border-down') || document;
                                var cands = activeCal.querySelectorAll(
                                    '.k-nav-fast, .k-calendar-header .k-title, .k-calendar .k-header .k-title'
                                );
                                for (var i = 0; i < cands.length; i++) {
                                    var r = cands[i].getBoundingClientRect();
                                    if (r.width > 0 && r.height > 0) return cands[i].innerText || '';
                                }
                                return '';
                            """) or ""
                            
                            self._log(logging.DEBUG, f"    [NAV] Mevcut ay: '{title_text.strip()}' | Hedef: '{target_month}'")
                            
                            # title_text = "March 2026" ve target_month = "March 2026" şeklinde olmalı
                            # Ayın adını ve yılını karşılaştır (büyük/küçük harf duyarsız)
                            target_parts = target_month.lower().split()
                            title_lower = title_text.lower()
                            if all(p in title_lower for p in target_parts):
                                self._log(logging.INFO, f"  [CAL] Dogru ayda: '{title_text.strip()}'")
                                break
                            
                            # Görünür Next butonunu bul ve tıkla
                            next_ok = self.driver.execute_script("""
                                function isReal(el) {
                                    var r = el.getBoundingClientRect();
                                    if (r.width <= 0 || r.height <= 0) return false;
                                    if (r.right < -50) return false;
                                    var c = el;
                                    while (c) {
                                        var s = window.getComputedStyle(c);
                                        if (s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;
                                        c = c.parentElement;
                                    }
                                    return true;
                                }
                                var activeCal = document.querySelector('.k-animation-container .k-calendar-container, .k-calendar-container.k-state-border-down') || document;
                                var btn = Array.from(activeCal.querySelectorAll(
                                    '.k-nav-next, .k-calendar-nav-next, [aria-label="Next"]'
                                )).find(isReal);
                                if (btn) {
                                    btn.scrollIntoView({block: 'center', behavior: 'instant'});
                                    btn.click(); 
                                    return true; 
                                }
                                return false;
                            """)
                            
                            if not next_ok:
                                self._log(logging.WARNING, f"  [CAL] 'Sonraki Ay' butonu bulunamadi (deneme {nav_attempt+1}), yeniden deneniyor...")
                                time.sleep(0.5)
                                continue  # break değil continue: takvim yeniden render olmuş olabilir
                            
                            # Ay değişiminin DOM'a yansımasını bekle
                            try:
                                WebDriverWait(self.driver, 4).until(
                                    EC.staleness_of(self.driver.find_element(By.CSS_SELECTOR, "table[role='grid']"))
                                )
                            except Exception:
                                time.sleep(1.0)
                        else:
                            self._log(logging.WARNING, f"  [CAL] 18 denemede '{target_month}' ayina ulasılamadi!")
                    
                    # ─── ADIM 3: YEŞİL GÜNÜ BUL VE TIKLA ─────────────────────────────
                    # Anahtar: $(link).trigger('click') - Kendo'nun kendi event handler'ını çağırır.
                    # Bu, BLS sitesinde subagent tarafından KANITLANMIŞ tek çalışan yöntemdir.
                    self._log(logging.INFO, f"  [CAL] Adim 3/3: Takvimde gun '{day_num}' aranip jQuery trigger ile tiklaniyor...")
                    
                    # Takvim grid'inin güncel DOM'da olduğunu garantile
                    try:
                        WebDriverWait(self.driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "td[role='gridcell'] .k-link"))
                        )
                    except Exception:
                        pass
                    
                    click_result = self.driver.execute_script("""
                        var day_num = arguments[0];
                        var activeCal = document.querySelector('.k-animation-container .k-calendar-container, .k-calendar-container.k-state-border-down') || document;
                        
                        // SADECE aktif takvimin (k-other-month olmayan), devre dışı olmayan hücrelerini tara
                        var cells = activeCal.querySelectorAll(
                            "td[role='gridcell']:not(.k-other-month):not(.k-state-disabled) .k-link"
                        );
                        
                        var processedCells = [];
                        
                        for (var i = 0; i < cells.length; i++) {
                            var link = cells[i];
                            var txt = link.textContent.trim();
                            
                            var r = link.getBoundingClientRect();
                            var bg = window.getComputedStyle(link).backgroundColor;
                            
                            if (txt === day_num) {
                                processedCells.push(txt + (r.width>0?'(VIS)':'(HID)') + '[' + bg + ']');
                                
                                // Boyut 0 ise atla
                                if (r.width <= 0 || r.height <= 0) continue;
                                
                                var nums = bg.match(/[0-9]+/g);
                                if (!nums || nums.length < 3) continue;
                                var R = parseInt(nums[0]), G = parseInt(nums[1]), B = parseInt(nums[2]);
                                
                                // Yeşil renk: G > 100 VE G > R'nin 2 katı
                                if (G <= 100 || G <= R * 2) continue;
                                
                                // TIKLA: jQuery trigger
                                link.scrollIntoView({block: 'center', behavior: 'instant'});
                                if (typeof $ !== 'undefined') {
                                    $(link).trigger('click');
                                    return 'OK:' + txt + ':' + bg;
                                } else {
                                    link.parentElement.click();
                                    return 'TD_CLICK:' + txt;
                                }
                            }
                        }
                        
                        return 'NOT_FOUND|cells=' + processedCells.join(',');
                    """, str(day_num))
                    
                    self._log(logging.INFO, f"  [CAL] click_result: {click_result}")
                    
                    if not click_result or str(click_result).startswith('NOT_FOUND'):
                        self._log(logging.ERROR, f"  [CAL] Gun '{day_num}' bulunamadi. Debug: {click_result}")
                        return False
                    
                    self._log(logging.INFO, f"  [CAL] Tarihe basarıyla tiklandi: {click_result}")
                    
                    date_clicked = True   # Tarih başarıyla seçildi
                    slot_selected = False  # Sıfırla (önemli, önceki değer olabilir)
                    
                    # ─── ADIM 4: AJAX'IN BİTMESİNİ BEKLE ─────────────────────────────
                    # blockUI spinner kaybolunca AJAX tamamdır - time.sleep değil, WebDriverWait!
                    self._log(logging.INFO, "  [CAL] AJAX spinner'i bekleniyor...")
                    
                    # SPINNER YAKALAMA HATASI DÜZELTME:
                    # Tarihe tıkladıktan sonra sitenin "Yükleniyor" ekranını çıkarması bazen 1 sn sürüyor.
                    # Eğer beklemeden invisibility (Görünmez mi?) diye sorarsak Selenium "Evet henüz ekranda yok!" deyip anında geçer.
                    time.sleep(1.5) 
                    
                    try:
                        WebDriverWait(self.driver, 15).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, ".blockUI.blockOverlay"))
                        )
                    except Exception:
                        self._log(logging.WARNING, "  [CAL] Spinner 15s'de kaybolmadi, devam ediliyor.")
                    
                    # DOM'un settle olmasını bekle (AJAX sonrası render)
                    try:
                        WebDriverWait(self.driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "[id*='Slot'], #ddlAppointmentSlot, select"))
                        )
                    except Exception:
                        time.sleep(1.5)
                                

                    # 5. Saat Seçimi - Önce AJAX'ın bitmesini ve slotların yüklenmesini bekle
                    self._log(logging.INFO, "  [SLOT] Tarih seçimi sonrası saat slotlarının yüklenmesi bekleniyor...")
                    
                    time.sleep(1.0) # Yukarıdaki delay yetmemiş olabilir diye ekstra güvenlik
                    
                    # Loading spinner kaybolunca AJAX bitti
                    try:
                        WebDriverWait(self.driver, 15).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, ".blockUI.blockOverlay, .k-loading-mask"))
                        )
                    except Exception:
                        pass
                    
                    # '--Select--' dropdown'unun görünmesini bekle (slotlar yüklendi)
                    try:
                        WebDriverWait(self.driver, 10).until(
                            lambda d: d.execute_script("""
                                function isVis(el){var c=el;while(c){var s=window.getComputedStyle(c);
                                if(s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0)return false;
                                c=c.parentElement;}return true;}
                                return Array.from(document.querySelectorAll('span,div'))
                                    .some(el=>el.textContent.trim()==='--Select--'&&isVis(el));
                            """)
                        )
                        self._log(logging.INFO, "  [SLOT] Saat dropdown'u hazır!")
                    except Exception:
                        self._log(logging.WARNING, "  [SLOT] 10s içinde --Select-- bulunamadı, yine de devam ediliyor...")
                    
                    time.sleep(2.5)  # DOM animasyon + AJAX sonrası render settle süresi
                    
                    self._log(logging.INFO, "  [SLOT] Saat dropdown'u açılıyor...")
                    
                    # ADIM 5a: Dropdown'u aç (--Select-- span/div'e tıkla)
                    opened_dropdown = self.driver.execute_script("""
                        function isReal(el) {
                            var r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) return false;
                            // Sadece tamamen ekran dışına taşınmış (sol negatif = honeypot) engelle
                            if (r.right < -50) return false;
                            var c = el;
                            while (c) {
                                var s = window.getComputedStyle(c);
                                if (s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;
                                c = c.parentElement;
                            }
                            return true;
                        }
                        // Dropdown'u viewport'a getir (sayfa altında olabilir)
                        var anyDd = document.querySelector('.k-dropdown .k-dropdown-wrap, .k-widget.k-dropdown');
                        if (anyDd) anyDd.scrollIntoView({block: 'center', behavior: 'instant'});
                        // Kendo DropDownList için .k-dropdown-wrap veya --Select-- içeren span'ı bul
                        var dropdown = Array.from(document.querySelectorAll(
                            '.k-dropdown .k-dropdown-wrap, .k-widget.k-dropdown'
                        )).find(isReal);
                        if (dropdown) {
                            if (typeof $ !== 'undefined') { $(dropdown).trigger('click'); }
                            else { dropdown.click(); }
                            return 'opened:dropdown-wrap';
                        }
                        // Fallback: --Select-- metnini içeren görünür öğe
                        var sel = Array.from(document.querySelectorAll('span, div')).find(
                            el => el.textContent.trim() === '--Select--' && isReal(el)
                        );
                        if (sel) {
                            if (typeof $ !== 'undefined') { $(sel).trigger('click'); }
                            else { sel.click(); }
                            return 'opened:select-text';
                        }
                        return 'NOT_FOUND';
                    """)
                    
                    self._log(logging.INFO, f"  [SLOT] Dropdown acildi: {opened_dropdown}")
                    
                    if not opened_dropdown or opened_dropdown == 'NOT_FOUND':
                        self._log(logging.WARNING, "  [SLOT] Dropdown bulunamadi!")
                        slot_selected = False
                    else:
                        # ADIM 5b: Seçeneklerin görünmesini ve animasyonun bitmesini bekle
                        # Kendo UI, dropdown'u açarken bir animasyon container kullanır. 
                        # Animasyon bitmeden tıklarsak, widget state bozulur ve sayfa takılır.
                        self._log(logging.INFO, "  [SLOT] Dropdown animasyonunun ve AJAX'in tamamlanmasi bekleniyor...")
                        
                        try:
                            # .k-animation-container div'inin gorunur duruma gelmesini bekliyoruz
                            WebDriverWait(self.driver, 5).until(
                                lambda d: d.execute_script("""
                                    var containers = document.querySelectorAll('.k-animation-container');
                                    for(var i=0; i<containers.length; i++) {
                                        var style = window.getComputedStyle(containers[i]);
                                        // Animasyon suresi bitince overflow:visible veya hidden gibi state degisikliklerine oturur
                                        // Biz sadece container'in display:block ve opacity > 0 oldugundan emin oluyoruz
                                        if(style.display !== 'none' && parseFloat(style.opacity) > 0) {
                                            return true;
                                        }
                                    }
                                    return false;
                                """)
                            )
                        except Exception:
                            self._log(logging.WARNING, "  [SLOT] Animasyon container timeout, devam ediliyor.")
                            time.sleep(1) # Fallback DOM settling
                        
                        # ADIM 5c: İlk geçerli saat seçeneğini Deterministic JS ile seç
                        # Ekranda acik olan popup'in icindeki listeyi bulmak icin 
                        # butun options'lari tarayip, honeypot viewport kontrolleri YERINE
                        # popup parentinin gorunurlugune bakacagiz (cunku popup fold disinda olabilir).
                        slot_selected = self.driver.execute_script("""
                            function isNodeTrueVisible(el) {
                                var r = el.getBoundingClientRect();
                                if (r.width <= 0 || r.height <= 0) return false;
                                // Popup'lar viewport disina tasabilir, o yuzden r.top > H kullanmiyoruz.
                                // Sadece gercekten gizlenmis veya "left:-9999px" olanlari eliyoruz.
                                if (r.right < -50) return false;
                                var c = el;
                                while (c) {
                                    var s = window.getComputedStyle(c);
                                    if (s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;
                                    c = c.parentElement;
                                }
                                return true;
                            }
                            
                            // Acik popup olan listbox'lari (k-list-container) bul
                            var activeLists = Array.from(document.querySelectorAll('.k-animation-container .k-list-container, .k-widget.k-listbox'))
                                .filter(isNodeTrueVisible);
                            
                            if (activeLists.length === 0) {
                                return 'NO_OPEN_POPUP';
                            }
                            
                            var activeList = activeLists[0];
                            // '--Select--' harici, sadece YESIL (bos) olan tum optionlari bul
                            var options = Array.from(activeList.querySelectorAll('li[role="option"]')).filter(function(li) {
                                var t = li.textContent.trim();
                                if (t === '--Select--' || t === '' || li.classList.contains('k-state-disabled') || !isNodeTrueVisible(li)) return false;
                                
                                // Kirmizi/dolu slotlari secme hatasini onlemek icin background rengine bak:
                                // Option icinde div varsa onun rengine, yoksa li'nin rengine bakilir
                                var colorNode = li.querySelector('div') || li;
                                var bg = window.getComputedStyle(colorNode).backgroundColor;
                                var nums = bg.match(/[0-9]+/g);
                                
                                if (nums && nums.length >= 3) {
                                    var R = parseInt(nums[0]), G = parseInt(nums[1]), B = parseInt(nums[2]);
                                    
                                    // SADECE Gorkemli Yesil (G > 100 ve G > R'nin 1.25 kati) ise musaittir.
                                    // Kirmizi (R > 200) veya Sari (R>200, G>200) dolar.
                                    // Eger saydam degilse (R=0,G=0,B=0,A=0), kirmizi atla
                                    if (R !== 0 || G !== 0 || B !== 0) {
                                        if (G <= 100 || G <= R * 1.25) return false;
                                    }
                                }
                                return true;
                            });
                            
                            if (options.length > 0) {
                                var opt = options[0];
                                // Ilgili dropdown objesini Kendo API uzerinden tetiklemek daha saglikli
                                // ama once UI uzerinden tiklayacagiz. jQuery'nin trigger click'i kendo eventlerini dogru baglar.
                                opt.scrollIntoView({block: 'nearest', behavior: 'instant'});
                                if (typeof $ !== 'undefined') { $(opt).trigger('click'); }
                                else { opt.click(); }
                                return 'SELECTED:' + opt.textContent.trim();
                            }
                            
                            // Debug: mevcut li'leri dondur
                            return 'NO_VALID_OPTIONS|tot=' + activeList.querySelectorAll('li').length;
                        """)
                        
                        if isinstance(slot_selected, str) and slot_selected.startswith('SELECTED'):
                            self._log(logging.INFO, f"  [SLOT] Saat basariyla secildi: {slot_selected}")
                            slot_selected = True
                            # Kendo UI'ın slot seçimini kaydetmesi için gölge eventleri tetikleyelim
                            self.driver.execute_script("""
                                try {
                                    var dd = document.querySelector('select#ddlAppointmentSlot, input#ddlAppointmentSlot');
                                    if(dd && typeof $ !== 'undefined') { $(dd).trigger('change'); }
                                } catch(e){}
                            """)
                        else:
                            self._log(logging.WARNING, f"  [SLOT] Saat secilemedi. Debug sonucu: {slot_selected}")
                            slot_selected = False

                    
                    # Submit butonuna basmadan önce UI'ın seçimi sindirmesi için 3 saniye bekle
                    time.sleep(3)
                    
                    if date_clicked and slot_selected:
                        self._log(logging.INFO, "  >> Submit (Onayla) tuşuna Python üzerinden basılıyor.")
                        
                        # Sadece gorunur olan Submit butonunu bul (Honeypot korumasi JS Treewalk)
                        js_submit = """
                            function isVisible(el) {
                                var curr = el;
                                while (curr) {
                                    var style = window.getComputedStyle(curr);
                                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
                                    curr = curr.parentElement;
                                }
                                return true;
                            }
                            var submitBtn = document.querySelector('#btnSubmit, input[value="Submit"], input[type="submit"]');
                            if (submitBtn && isVisible(submitBtn)) {
                                submitBtn.click();
                                return true;
                            }
                            return false;
                        """
                        submit_success = self.driver.execute_script(js_submit)
                        if submit_success:
                             self._log(logging.INFO, "  >> Submit tuşuna başarıyla NATIVE JS ile tıklandı.")
                        else:
                             self._log(logging.ERROR, "  >> [KRİTİK NATIVE HATA] Görünür bir 'Submit' tuşu bulunamadı (Honeypot müdahalesi?).")
                    
                    time.sleep(4) # Submit sonrası diğer sayfaya geçiş için kilit bekleme
                except Exception as e:
                    self._log(logging.ERROR, f"  Takvim hatası: {e}")
                    date_clicked = False
                    slot_selected = False
                    
                if not date_clicked or not slot_selected:
                    self._log(logging.WARNING, "  Takvim/Zaman seçilemedi. İptal ediliyor")
                    return False

            # --- 4) Applicant Selection / Travel Details / Photo Upload / OTP ---
            current_url = self.driver.current_url
            if "ApplicantSelection" in current_url:
                self._log(logging.INFO, "  >> Applicant Selection sayfası algılandı.")

                try:
                    # 1. HATA DÜZELTME: Consent (Terms of Service) Modal'ını Kapatma
                    self._log(logging.INFO, "  [APPLICANT] Terms of Service / Consent Modalı kontrol ediliyor...")
                    self.driver.execute_script("""
                        try {
                            var consentBtn = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn')).find(el => {
                                var txt = (el.innerText || el.value || '').toLowerCase();
                                return txt.includes('i agree') || txt.includes('accept') || txt.includes('kabul');
                            });
                            
                            if (consentBtn) {
                                var rect = consentBtn.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    consentBtn.scrollIntoView({block: 'center', behavior: 'instant'});
                                    consentBtn.click();
                                    return true;
                                }
                            }
                        } catch(e) {}
                        return false;
                    """)
                    time.sleep(1.5) # Modal kapanma animasyonu

                    # 2. HATA DÜZELTME: Travel Date, Arrival Date, Departure Date Doldurma (JS Injection ile)
                    self._log(logging.INFO, "  [APPLICANT] Seyahat tarih alanları dolduruluyor...")
                    self.driver.execute_script("""
                        try {
                            var datePickers = document.querySelectorAll("input[data-role='datepicker']");
                            var today = new Date();
                            
                            var travelDate = new Date(); travelDate.setDate(today.getDate() + 30);
                            var depDate = new Date(); depDate.setDate(today.getDate() + 60);

                            for (var i = 0; i < datePickers.length; i++) {
                                var widget = $(datePickers[i]).data("kendoDatePicker");
                                if (widget) {
                                    var inputId = datePickers[i].id.toLowerCase();
                                    if (inputId.indexOf("travel") !== -1 || inputId.indexOf("arrival") !== -1) {
                                        widget.value(travelDate); widget.trigger("change");
                                    } else if (inputId.indexOf("departure") !== -1) {
                                        widget.value(depDate); widget.trigger("change");
                                    } else {
                                        widget.value(travelDate); widget.trigger("change");
                                    }
                                }
                            }
                        } catch(e) { }
                    """)
                    time.sleep(1)

                    # 3. HATA DÜZELTME: Adayı Listeden Seçmek (RadioButton - ApplicantId)
                    self._log(logging.INFO, "  [APPLICANT] Aday listeden seciliyor...")
                    self.driver.execute_script("""
                        try {
                            // Genellikle name="ApplicantId" veya type="radio" olur
                            var radios = document.querySelectorAll("input[type='radio'][name*='ApplicantId'], input[type='radio']");
                            for (var i = 0; i < radios.length; i++) {
                                var r = radios[i].getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    radios[i].scrollIntoView({block: 'center', behavior: 'instant'});
                                    radios[i].click();
                                    if (typeof $ !== 'undefined') { $(radios[i]).trigger('change'); }
                                    return true;
                                }
                            }
                        } catch (e) {}
                        return false;
                    """)
                    time.sleep(1)

                    # Photo Upload Bölümü
                    self._log(logging.INFO, "  Stock vesikalık fotoğraf yükleniyor...")
                    file_input = self._find_element_multi([
                        (By.ID, "PassportCopy"),
                        (By.XPATH, "//input[@type='file']")
                    ], timeout=3)
                    
                    if file_input:
                        stock_photo_path = os.path.join(os.getcwd(), "test_photo.jpg")
                        if not os.path.exists(stock_photo_path):
                            # Dosya yoksa dummy bir dosya yarat (Sistemi kandırmak için)
                            with open(stock_photo_path, "wb") as f:
                                # Geçerli bir JPEG header'ı ile 1 piksel oluştur
                                f.write(b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xFF\xDB\x00C\x00\x05\x03\x04\x04\x04\x03\x05\x04\x04\x04\x05\x05\x05\x06\x07\x0C\x08\x07\x07\x07\x0F\x0B\x0B\t\x0C\x11\x0F\x12\x12\x11\x0F\x11\x11\x13\x16\x1C\x17\x13\x14\x1A\x15\x11\x11\x18!\x18\x1A\x1D\x1D\x1F\x1F\x1F\x13\x17"$x\x1E$\x1C\x1E\x1F\x1E\xFF\xDB\x00C\x01\x05\x05\x05\x07\x06\x07\x0E\x08\x08\x0E\x1E\x14\x11\x14\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E\xFF\xC0\x00\x0B\x08\x00\x01\x00\x01\x03\x01"\x00\x02\x11\x01\x03\x11\x01\xFF\xC4\x00\x1F\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0B\xFF\xC4\x00\xB5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xA1\x08#B\xB1\xC1\x15R\xD1\xF0$3br\x82\t\n\x16\x17\x18\x19\x1A%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8A\x92\x93\x94\x95\x96\x97\x98\x99\x9A\xA2\xA3\xA4\xA5\xA6\xA7\xA8\xA9\xAA\xB2\xB3\xB4\xB5\xB6\xB7\xB8\xB9\xBA\xC2\xC3\xC4\xC5\xC6\xC7\xC8\xC9\xCA\xD2\xD3\xD4\xD5\xD6\xD7\xD8\xD9\xDA\xE1\xE2\xE3\xE4\xE5\xE6\xE7\xE8\xE9\xEA\xF1\xF2\xF3\xF4\xF5\xF6\xF7\xF8\xF9\xFA\xFF\xC4\x00\x1F\x01\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0B\xFF\xC4\x00\xB5\x11\x00\x02\x01\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02w\x00\x01\x02\x03\x11\x04\x05!1\x06\x12AQ\x07aq\x13"2\x81\x08\x14B\x91\xA1\xB1\xC1\t#3R\xF0\x15br\xD1\n\x16$4\xE1%\xF1\x17\x18\x19\x1A&\'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85\x86\x87\x88\x89\x8A\x92\x93\x94\x95\x96\x97\x98\x99\x9A\xA2\xA3\xA4\xA5\xA6\xA7\xA8\xA9\xAA\xB2\xB3\xB4\xB5\xB6\xB7\xB8\xB9\xBA\xC2\xC3\xC4\xC5\xC6\xC7\xC8\xC9\xCA\xD2\xD3\xD4\xD5\xD6\xD7\xD8\xD9\xDA\xE2\xE3\xE4\xE5\xE6\xE7\xE8\xE9\xEA\xF2\xF3\xF4\xF5\xF6\xF7\xF8\xF9\xFA\xFF\xDA\x00\x0C\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xF5Z\xA8\xBF\xFF\xD9')

                        file_input.send_keys(stock_photo_path)
                        self._log(logging.INFO, "  Fotoğraf seçildi: " + stock_photo_path)
                        
                        # Upload Trigger button
                        btn_upload = self._find_element_multi([
                            (By.ID, "btnUpload"),
                            (By.XPATH, "//input[@value='Upload']")
                        ], timeout=2)
                        if btn_upload:
                            try: btn_upload.click()
                            except: self.driver.execute_script("arguments[0].click();", btn_upload)
                            time.sleep(2)
                    
                    # OTP Talebi ve Okunması
                    try:
                        self._log(logging.INFO, "  [OTP] Aday seçim sayfasına ulaşıldı, sistem otomatik olarak onay kodu yolluyor...")
                        self._log(logging.INFO, "  [OTP] Lütfen bekleyin... E-posta adresinize (IMAP) gelen şifre bekleniyor. (Maks 3 Dakika)")


                        # EmailReader Modülü'nü Çağır (3 Dakika Süre Ver)
                        # E-posta parametreleri GlobalUserCredentials içinden alınır
                        from bot.email_reader import OTPReader
                            
                        # Manager.py'dan email_creds parametresi global statelerden geliyor olabilir
                        # Şimdilik kullanıcı ayarlarına fallback yapacağız.
                        email_address = self.user_data.get("email")
                        app_password  = self.user_data.get("email_app_password")
                        
                        # 🚨 IMAP YÖNLENDİRME (ALIAS) DESTEĞİ 🚨
                        # Eğer kullanıcı şifre kutusuna "fthctlcm2004@gmail.com:ckfszpqdeycyljoj" 
                        # formatında girdiyse, BLS Login için asıl maili (Outlook) kullanmaya devam edip 
                        # OTP okuması (IMAP) için bu kutudaki yazan Gmail'i kullanacağız.
                        if app_password and ":" in app_password:
                            parts = app_password.split(":", 1)
                            if "@" in parts[0]:
                                email_address = parts[0].strip()
                                app_password = parts[1].replace(" ", "").strip()
                                self._log(logging.INFO, f"  [OTP] Uygulama Şifresi kutusunda Yönlendirme Maili (Alias) algılandı: {email_address}")

                        if not email_address or not app_password:
                            self._log(logging.ERROR, "  [OTP] ❌ IMAP için E-posta adresi veya Uygulama Şifresi ayarlanmamış! Otomatik okuma yapılamaz.")
                        else:
                            # Şifre gerçekten çözüldü mü yoksa şifreli (b'gAAAA...') veya yanlış mı geliyor, güvenlik açısından maskeleyip görelim.
                            masked_pw = f"{app_password[:2]}***{app_password[-2:]}" if len(app_password) > 4 else "***"
                            self._log(logging.INFO, f"  [OTP] IMAP Şifresi Kontrolü: Uzunluk={len(app_password)}, Baş/Son={masked_pw}")

                            # Ekrana basılan hatayı kolayca izlemek için bir hook verelim
                            def otp_log_hook(lvl, msg):
                                self._log(lvl, f"  [OTP] {msg}")

                            reader = OTPReader(email_address, app_password, log_func=otp_log_hook)
                            otp_code = reader.wait_for_otp(timeout=180, poll_interval=10)
                                
                            if otp_code:
                                self._log(logging.INFO, f"  [OTP] ✅ Gelen Kutusu (IMAP) Üzerinden Şifre Başarıyla Okundu: {otp_code}")
                                self._log(logging.INFO, "  [OTP] Şifre ekrana giriliyor...")
                                
                                # Subagent onayı: Kutu id'si "EmailCode" ve Kendo Widgetı değil.
                                js_inject_otp = f"""
                                    try {{
                                        var otpInput = document.getElementById('EmailCode') || document.getElementById('OTPCode') || document.querySelector('input[name*="EmailCode"]');
                                        if (otpInput) {{
                                            otpInput.value = '{otp_code}';
                                            otpInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                            otpInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                            otpInput.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                                            return true;
                                        }}
                                    }} catch(e) {{}}
                                    return false;
                                """
                                self.driver.execute_script(js_inject_otp)
                                time.sleep(1.0)
                                
                                # 3. Selenium Fallback (JS'nin tetikleyemediği backend dinleyicileri için)
                                try:
                                    otp_elem = self._find_element_multi([
                                        (By.ID, "EmailCode"), (By.ID, "OTPCode"), (By.XPATH, "//input[contains(@name, 'EmailCode')]")
                                    ], timeout=2)
                                    if otp_elem:
                                        otp_elem.click()
                                        time.sleep(0.5)
                                        otp_elem.send_keys(Keys.CONTROL + "a")
                                        otp_elem.send_keys(Keys.DELETE)
                                        otp_elem.send_keys(otp_code)
                                        self._log(logging.INFO, "  [OTP] Selenium fallback ile OTP yazıldı.")
                                        time.sleep(0.5)
                                except Exception as e:
                                    self._log(logging.DEBUG, f"  [OTP] Selenium fallback hatası: {e}")
                                
                                # Verify Butonu Yoksa da sorun değil, bazen otomatik geçiyor. 
                                # Yeni sistemde genelde "Verify OTP" veya "btnVerifyOTP" id'si olur.
                                btn_verify = self._find_element_multi([
                                    (By.ID, "btnVerifyOTP"),
                                    (By.ID, "btnVerify"),
                                    (By.XPATH, "//input[@value='Verify OTP']"),
                                    (By.XPATH, "//button[contains(text(), 'Verify')]")
                                ], timeout=2)
                                
                                if btn_verify:
                                    self._log(logging.INFO, "  [OTP] 'Verify / Doğrula' butonuna basılıyor.")
                                    try: btn_verify.click()
                                    except: self.driver.execute_script("arguments[0].click();", btn_verify)
                                    time.sleep(3)
                            else:
                                self._log(logging.ERROR, "  [OTP] ❌ Verilen 3 dakika içinde IMAP'e herhangi bir OTP maili düşmedi.")
                    except Exception as otp_e:
                        self._log(logging.WARNING, f"  [OTP] Hatası: {otp_e}")

                    # Final Devam/Submit (Applicant Selection)
                    btn_submit = self._find_element_multi([
                        (By.ID, "btnSubmit"),
                        (By.TAG_NAME, "button")
                    ])
                    if btn_submit:
                        self._log(logging.INFO, "  >> Submit/Proceed (Applicant Selection) butonuna basılıyor.")
                        self.driver.execute_script("arguments[0].click();", btn_submit)
                        time.sleep(5)
                except Exception as ex:
                    self._log(logging.ERROR, f"  Applicant Selection adımında hata: {ex}")
                    return False

            # --- 5) Extra Services / VAS Services ---
            current_url = self.driver.current_url
            if "VASSelection" in current_url or "Premium" in current_url or "Service" in current_url:
                self._log(logging.INFO, "  >> Extra Services (VASSelection) sayfası algılandı. Atlanıyor (Skip).")
                try:
                    self.driver.execute_script("""
                        var skipBtns = document.querySelectorAll("input[value='Skip'], button:contains('Skip'), a:contains('Skip')");
                        if(skipBtns.length > 0) { skipBtns[0].click(); }
                        else {
                           var btn = document.getElementById("btnSkip");
                           if(btn) btn.click();
                        }
                    """)
                    time.sleep(3)
                except Exception as e:
                    self._log(logging.ERROR, f"VASSelection atlama hatası: {e}")

            # --- 6) Payment / Final Booking Confirmation ---
            current_url = self.driver.current_url
            if "Payment" in current_url or "Booking" in current_url or "Confirm" in current_url:
                self._log(logging.INFO, "  >> Payment / Booking Confirmation sayfası algılandı.")
                
                # Gizlilik Politikası Onay Kutusu (Eğer Varsa)
                try:
                    self.driver.execute_script("""
                        var chk = document.getElementById("PrivacyPolicy");
                        if(chk && !chk.checked) chk.click();
                        var chk2 = document.getElementById("TermsAndConditions");
                        if(chk2 && !chk2.checked) chk2.click();
                    """)
                except: pass

                # Book Appointment Button
                btn_book = self._find_element_multi([
                    (By.ID, "btnBook"),
                    (By.ID, "btnSubmit"),
                    (By.XPATH, "//input[@value='Book Appointment']"),
                    (By.XPATH, "//input[@value='Pay Now']"),
                    (By.XPATH, "//input[contains(@value, 'Book')]")
                ], timeout=3)
                
                if btn_book:
                    self._log(logging.INFO, "  >> Son 'Book Appointment' (veya Pay) tuşuna basılıyor...")
                    try:
                        btn_book.click()
                    except:
                        self.driver.execute_script("arguments[0].scrollIntoView(); arguments[0].click();", btn_book)
                else:
                    self._log(logging.WARNING, "  Final sayfasında Book butonu bulunamadı, script ile Form post ediliyor...")
                    try:
                        self.driver.execute_script("document.forms[0].submit();")
                    except: pass
            
            # ── Başarı kontrolü (Congratulations) ─────────────────────────────
            current_url = self.driver.current_url.lower()
            if "payment" in current_url or "booking" in current_url or "confirm" in current_url or "success" in current_url or "receipt" in current_url:
                try:
                    time.sleep(5) # Son sayfanın (Congratulations) yüklenmesini bekle
                    page_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                    success_keywords = ["success", "congratulations", "başarı", "onaylandı", "appointment booked", "booking confirmed", "appointment summary"]
                    
                    if any(kw in page_text for kw in success_keywords):
                        self._log(logging.INFO, "🎉 RANDEVU BAŞARIYLA ALINDI! (Congratulations ekranı yakalandı)")
                        METRIC_BOOKING_SUCCESS.inc()
                        
                        # Randevu detaylarını JS ile çekmeye çalış (Discord/DB Tarafı İçin)
                        booking_info = self.driver.execute_script("""
                            var info = { no: "Bulunamadı", date: "Bulunamadı", time: "Bulunamadı" };
                            // Genellikle tablo (th/td) veya span formatındadır
                            var ths = document.querySelectorAll("td, th, span, div");
                            for (var i = 0; i < ths.length; i++) {
                                var t = ths[i].innerText.toLowerCase();
                                if (t.indexOf("appointment no") !== -1 || t.indexOf("reference") !== -1) {
                                    info.no = ths[i].nextElementSibling ? ths[i].nextElementSibling.innerText.trim() : (ths[i+1] ? ths[i+1].innerText.trim() : info.no);
                                }
                                if (t.indexOf("appointment date") !== -1 && t === "appointment date:") {
                                    info.date = ths[i].nextElementSibling ? ths[i].nextElementSibling.innerText.trim() : (ths[i+1] ? ths[i+1].innerText.trim() : info.date);
                                }
                                if (t.indexOf("appointment time") !== -1) {
                                    info.time = ths[i].nextElementSibling ? ths[i].nextElementSibling.innerText.trim() : (ths[i+1] ? ths[i+1].innerText.trim() : info.time);
                                }
                            }
                            // Sayfadan okuyamazsa URL'deki referansı deneyelim
                            if (info.no === "Bulunamadı" && window.location.href.indexOf("ref=") !== -1) {
                                info.no = window.location.href.split("ref=")[1].split("&")[0];
                            }
                            return info;
                        """)
                        
                        log_msg = f"📝 Randevu Detayları: ID=[{booking_info.get('no')}], Date=[{booking_info.get('date')}], Time=[{booking_info.get('time')}]"
                        self._log(logging.INFO, log_msg)
                        return True
                    else:
                        self._log(logging.WARNING, "Submission gerçekleşti ancak onay ekranı (Congratulations) gelmedi veya site yavaşlıktan çöktü. Tarama devam edecek...")
                        return False  # İşlem tamamlanmadı say ve tekrar denenmesine izin ver
                except Exception as e:
                    self._log(logging.ERROR, f"Başarı ekranı kontrol hatası (Site yüklenmemiş olabilir): {e}")
                    return False
            else:
                self._log(logging.WARNING, f"Randevu alımı onay sayfasına ulaşamadı. Kalan URL: {current_url}")
                return False

        except Exception as e:
            self._log(logging.ERROR, f"Randevu alma genel hatası: {e}")
            return False
