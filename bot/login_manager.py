"""
Login Manager Module — Authentication flow for BLS Spain.

Extracted from BLSScraper: login(), register(), cookie management,
password field detection, CAPTCHA integration, and login success checks.
"""
import os
import time
import json
import logging
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bot.captcha_solver import CaptchaSolver
from bot.proxy_manager import proxy_manager
from config.database import report_account_risk, get_account_risk
from bot.telemetry import METRIC_LOGIN_ATTEMPTS, METRIC_PAGE_LOAD_LATENCY

logger = logging.getLogger(__name__)

LOGIN_URL = "https://turkey.blsspainglobal.com/Global/account/login"
REGISTER_URL = "https://turkey.blsspainglobal.com/Global/account/register"
APPOINTMENT_URL = "https://turkey.blsspainglobal.com/Global/bls/visatype"


class LoginManager:
    """Handles login, registration, cookie session management, and form field discovery."""

    def __init__(self, user_data: dict, config: dict, log_func=None):
        self.user_data = user_data
        self.config = config
        self.log_func = log_func
        self.driver = None  # Set by orchestrator after BrowserFactory creates it
        self.is_logged_in = False

        # Cookie session paths
        email = user_data.get("email", "unknown")
        safe_email = email.replace("@", "_at_").replace(".", "_")
        self._session_dir = os.path.join("data", "sessions")
        self._session_file = os.path.join(self._session_dir, f"{safe_email}_cookies.json")

    def _log(self, level, msg):
        if self.log_func:
            self.log_func(level, msg)
        else:
            logger.log(level, f"[{self.user_data.get('first_name', '?')}] {msg}")

    def set_driver(self, driver):
        """Called by orchestrator once the driver is ready."""
        self.driver = driver

    # ─── Cookie Management ──────────────────────────────────────────────────

    def save_cookies(self):
        """Save browser cookies to disk for session reuse."""
        if not self.driver:
            return
        try:
            os.makedirs(self._session_dir, exist_ok=True)
            cookies = self.driver.get_cookies()
            with open(self._session_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            self._log(logging.INFO, f"🔍 {len(cookies)} cookie kaydedildi: {self._session_file}")
        except Exception as e:
            self._log(logging.DEBUG, f"Cookie kaydetme hatası: {e}")

    def load_cookies(self) -> bool:
        """Load saved cookies and verify session validity."""
        if not self.driver:
            return False
            
        try:
            # 1. Önce native tarayıcı profilinin (user-data-dir) oturumunu kontrol et
            self.driver.get(APPOINTMENT_URL)
            time.sleep(3)
            current_url = self.driver.current_url.lower()
            if 'login' not in current_url and 'account' not in current_url:
                self._log(logging.INFO, "✅ Native Profil oturumu geçerli! Login atlanıyor.")
                self.is_logged_in = True
                return True
                
            # 2. Native oturum yoksa, JSON olarak kaydedilmiş yedeği dene
            if not os.path.exists(self._session_file):
                return False
                
            self.driver.get(LOGIN_URL)
            time.sleep(2)
            with open(self._session_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            for cookie in cookies:
                cookie.pop('sameSite', None)
                cookie.pop('storeId', None)
                try:
                    self.driver.add_cookie(cookie)
                except Exception:
                    pass
                    
            self._log(logging.INFO, f"🔍 {len(cookies)} yedek cookie yüklendi, oturum tekrar kontrol ediliyor...")
            self.driver.get(APPOINTMENT_URL)
            time.sleep(3)
            current_url = self.driver.current_url.lower()
            
            if 'login' not in current_url and 'account' not in current_url:
                self._log(logging.INFO, "✅ Yedek JSON Cookie oturumu geçerli! Login atlanıyor.")
                self.is_logged_in = True
                return True
            else:
                self._log(logging.INFO, "❌ Tüm oturumlar sona ermiş. Normal login yapılacak.")
                return False
                
        except Exception as e:
            self._log(logging.DEBUG, f"Oturum doğrulama hatası: {e}")
            return False

    # ─── Field Discovery ────────────────────────────────────────────────────

    def find_element_multi(self, selectors: list, timeout: int = 10):
        """Try multiple selectors, return the first visible match."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for by, value in selectors:
                try:
                    el = self.driver.find_element(by, value)
                    if el.is_displayed():
                        return el
                except Exception:
                    pass
            time.sleep(0.5)
        return None

    def _find_password_field(self):
        """Find password field — type='password' or visible second input."""
        try:
            pw_fields = self.driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            pw_fields = [f for f in pw_fields if f.is_displayed() and f.is_enabled()]
            if pw_fields:
                return pw_fields[0]
        except Exception:
            pass
        visible = self._get_visible_text_inputs()
        if len(visible) >= 2:
            return visible[1]
        if len(visible) == 1:
            return visible[0]
        return None

    def _get_visible_text_inputs(self) -> list:
        """Get visible, fillable text/password inputs on the page."""
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
        """Find a visible button whose text matches one of the given strings."""
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

    def _solve_captcha_with_fallback(self, captcha_solver):
        """Solve CAPTCHA; if fails, give user 15s for manual solve."""
        captcha_ok = captcha_solver.solve()
        if not captcha_ok:
            self._log(logging.WARNING, "CAPTCHA otomatik çözülemedi — lütfen tarayıcıdan manuel çözün (15 sn)")
            for _ in range(15):
                time.sleep(1)
                if not captcha_solver.is_captcha_present():
                    self._log(logging.INFO, "CAPTCHA manuel olarak çözüldü")
                    break

    def _check_login_success(self, log: bool = False) -> bool:
        """Check URL to determine if login succeeded."""
        try:
            url = self.driver.current_url.lower()
            if "login" not in url:
                self.is_logged_in = True
                if log:
                    self._log(logging.INFO, f"✅ Giriş başarılı! URL: {url}")
                METRIC_LOGIN_ATTEMPTS.labels('success').inc()
                return True
            if log:
                for sel in [".validation-summary-errors", ".field-validation-error",
                            ".alert-danger", ".error-message"]:
                    try:
                        errs = self.driver.find_elements(By.CSS_SELECTOR, sel)
                        for e in errs:
                            if e.text.strip():
                                self._log(logging.ERROR, f"Hata: {e.text.strip()}")
                    except Exception:
                        pass
                self._log(logging.ERROR, f"Giriş başarısız — URL: {url}")
                METRIC_LOGIN_ATTEMPTS.labels('fail').inc()
                proxy_manager.report_failure(self.user_data.get("proxy_address"))
                report_account_risk(self.user_data.get('id'), 15, reason="Giriş Hatası (Login Fail)")
        except Exception:
            pass
        return False

    # ─── Login Flow ─────────────────────────────────────────────────────────

    def login(self, email: str, password: str, solve_captcha: bool = True) -> bool:
        """BLS login — state-machine approach with CAPTCHA retry loop."""
        try:
            self._log(logging.INFO, "Giriş yapılıyor...")
            start_time = time.time()
            self.driver.get(LOGIN_URL)
            end_time = time.time()

            latency_ms = (end_time - start_time) * 1000
            proxy_manager.report_latency(self.user_data.get("proxy_address"), latency_ms)
            METRIC_PAGE_LOAD_LATENCY.observe(end_time - start_time)
            logger.info(f"Page Load Latency: {latency_ms:.0f}ms")
            time.sleep(2)

            # Step 1: Email field
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

            # Step 2: Verify button
            verify_btn = self._find_visible_button(["Verify", "verify", "VERIFY"])
            if verify_btn:
                self._log(logging.INFO, "Verify tıklanıyor...")
                self.driver.execute_script("arguments[0].click();", verify_btn)
                time.sleep(3)
                if self._check_login_success():
                    return True
                if solve_captcha:
                    api_key = self.config.get("2captcha_key", "").strip()
                    captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                    if captcha_solver.is_captcha_present():
                        if self._find_password_field():
                            self._log(logging.INFO, "Captcha ve Şifre alanı aynı anda tespit edildi. Önce şifre girilecek...")
                        else:
                            self._log(logging.INFO, "Sadece CAPTCHA var (Email onayı olabilir). Çözülüyor...")
                            captcha_solver.solve()
                            if self._check_login_success():
                                return True
                total_wait = 60 if not solve_captcha else 20
                self._log(logging.INFO, f"Şifre sayfası bekleniyor... ({total_wait/2} sn)")
                for _ in range(total_wait):
                    time.sleep(0.5)
                    if self._check_login_success():
                        return True
                    inputs = self._get_visible_text_inputs()
                    if inputs:
                        break
                    if solve_captcha and captcha_solver.is_captcha_present() and not self._find_password_field():
                        self._log(logging.DEBUG, "Hala Captcha var ama şifre yok...")
                    self._log(logging.DEBUG, "Şifre alanı henüz yok, bekleniyor...")

            # Step 3: Password + CAPTCHA retry loop
            max_login_attempts = 3
            for attempt in range(max_login_attempts):
                self._log(logging.INFO, f"Giriş/Şifre Denemesi: {attempt + 1}/{max_login_attempts}")
                time.sleep(1)
                if self._check_login_success():
                    return True

                password_field = self._find_password_field()
                if not password_field:
                    if attempt == 0:
                        self._log(logging.WARNING, "Şifre alanı bulunamadı — lütfen tarayıcıdan şifreyi girin (90 sn)")
                        for _ in range(90):
                            time.sleep(1)
                            if self._check_login_success():
                                self._log(logging.INFO, "✅ Kullanıcı manuel giriş yaptı!")
                                self.is_logged_in = True
                                return True
                            password_field = self._find_password_field()
                            if password_field:
                                break
                    if not password_field:
                        self._log(logging.ERROR, "Şifre alanı bulunamadı, yeniden deneniyor...")
                        continue

                if password_field:
                    try:
                        self.driver.execute_script(
                            "arguments[0].value = arguments[1]; "
                            "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                            password_field, password
                        )
                        self._log(logging.INFO, "Şifre girildi")
                        time.sleep(0.5)
                    except Exception as pe:
                        self._log(logging.ERROR, f"Şifre alanına yazılırken hata: {pe}")
                        continue

                if not solve_captcha:
                    self._log(logging.WARNING, "CAPTCHA ve Şifre (Varsa) Manuel Giriş için 20 sn bekleniyor...")
                    time.sleep(20)
                    if self._check_login_success():
                        return True
                    continue

                api_key = self.config.get("2captcha_key", "").strip()
                captcha_solver = CaptchaSolver(self.driver, api_key=api_key)
                if captcha_solver.is_captcha_present():
                    self._log(logging.INFO, "CAPTCHA çözülüyor (Şifre girildi, şimdi Submit edilecek)...")
                    if captcha_solver.solve():
                        self._log(logging.INFO, "Captcha Submit edildi. Sonuç bekleniyor...")
                        time.sleep(2)  # PERF: was 5s
                        if self._check_login_success(log=True):
                            return True
                        else:
                            self._log(logging.WARNING, "Captcha çözüldü ancak giriş başarılı olmadı.")
                    else:
                        self._log(logging.ERROR, "Captcha çözülemedi, tekrar denenecek.")
                else:
                    login_btn = self._find_visible_button(["Login", "Giriş"])
                    if login_btn:
                        self._log(logging.INFO, "CAPTCHA yok, Login butonuna tıklanıyor...")
                        self.driver.execute_script("arguments[0].click();", login_btn)
                        time.sleep(4)
                        if self._check_login_success(log=True):
                            return True
                    else:
                        self._log(logging.INFO, "CAPTCHA yok, Login butonu da yok. Bekleniyor...")
                        time.sleep(2)
                        if self._check_login_success(log=True):
                            return True

            return self._check_login_success(log=True)
        except Exception as e:
            import traceback
            self._log(logging.ERROR, f"Giriş hatası: {e}")
            self._log(logging.ERROR, f"Traceback:\n{traceback.format_exc()}")
            self.is_logged_in = False
            return False

    # ─── Registration ───────────────────────────────────────────────────────

    def register(self, email: str, password: str, first_name: str, last_name: str,
                 phone: str) -> bool:
        """Create a new BLS account."""
        try:
            self._log(logging.INFO, "Kayıt sayfasına gidiliyor...")
            self.driver.get(REGISTER_URL)
            time.sleep(2)
            wait = WebDriverWait(self.driver, 20)
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
                    field = wait.until(EC.presence_of_element_located((By.ID, field_id)))
                    field.clear()
                    field.send_keys(value)
                    time.sleep(0.3)
                except Exception as e:
                    self._log(logging.WARNING, f"Alan bulunamadı: {field_id} - {e}")
            try:
                register_btn = self.driver.find_element(By.ID, "btnRegister")
                register_btn.click()
            except NoSuchElementException:
                btns = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    if "register" in btn.text.lower() or "kayıt" in btn.text.lower():
                        btn.click()
                        break
            risk_score = get_account_risk(self.user_data.get('id'))
            delay = 2
            if risk_score > 30:
                delay = 4
            if risk_score > 50:
                delay = 6
            time.sleep(delay)
            time.sleep(3)
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
