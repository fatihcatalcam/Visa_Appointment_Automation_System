"""
BLS Spain Visa Bot - Scraper Orchestrator
Delegates browser lifecycle to BrowserFactory and auth to LoginManager.
Retains form filling, date scanning, and booking logic.
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
from bot.browser import BrowserFactory
from bot.login_manager import LoginManager

logger = logging.getLogger(__name__)


class BLSScraper:
    """BLS Spain Visa scraper — thin orchestrator delegating to focused modules."""
    LOGIN_URL = "https://turkey.blsspainglobal.com/Global/account/login"
    REGISTER_URL = "https://turkey.blsspainglobal.com/Global/account/register"
    APPOINTMENT_URL = "https://turkey.blsspainglobal.com/Global/appointment/newappointment"

    def __init__(self, user_data: dict, global_config: dict = None, log_func=None):
        self.user_data = user_data
        self.config = global_config or {}
        self.headless = bool(self.user_data.get('headless', True))
        self._custom_log = log_func
        
        # Determine Proxy via ProxyManager
        assigned_proxy = self.user_data.get('proxy_address', "").strip()
        self.proxy = proxy_manager.get_proxy(assigned_proxy=assigned_proxy)
        if self.proxy:
            self.user_data['proxy_address'] = self.proxy
            
        self.driver = None
        self.wait = None
        self.is_logged_in = False
        
        # Composition: delegate browser and login to extracted modules
        self._browser = BrowserFactory(user_data, self.config, log_func)
        self._browser.proxy = self.proxy
        self._login_mgr = LoginManager(user_data, self.config, log_func)

    def _log(self, level, msg):
        if self._custom_log:
            self._custom_log(level, msg)
        else:
            logger.log(level, f"[{self.user_data.get('first_name', 'Bilinmiyor')}] {msg}")

    # ═══════════════════════════════════════════════════════════════════════
    # Browser Lifecycle — delegated to BrowserFactory
    # ═══════════════════════════════════════════════════════════════════════

    def _generate_fingerprint(self):
        return self._browser.generate_fingerprint()

    def start_driver(self):
        """Launch Chrome via BrowserFactory and share the driver with LoginManager."""
        result = self._browser.create_driver()
        if result:
            self.driver = self._browser.driver
            self.wait = self._browser.wait
            self._login_mgr.set_driver(self.driver)
        return result

    def dump_network_logs(self):
        self._browser.dump_network_logs()

    def stop_driver(self):
        """Shut down WebDriver via LoginManager (save cookies) + BrowserFactory (quit)."""
        if self.driver:
            try:
                self._login_mgr.save_cookies()
            except Exception:
                pass
            self._browser.driver = self.driver
            self._browser.close_driver()
            self.driver = None
            self.is_logged_in = False

    # ═══════════════════════════════════════════════════════════════════════
    # Authentication — delegated to LoginManager
    # ═══════════════════════════════════════════════════════════════════════

    def _save_cookies(self):
        self._login_mgr.save_cookies()

    def _load_cookies(self):
        result = self._login_mgr.load_cookies()
        if result:
            self.is_logged_in = True
        return result

    def login(self, email: str, password: str, solve_captcha: bool = True) -> bool:
        result = self._login_mgr.login(email, password, solve_captcha)
        self.is_logged_in = self._login_mgr.is_logged_in
        return result

    def _check_login_success(self, log: bool = False) -> bool:
        result = self._login_mgr._check_login_success(log)
        self.is_logged_in = self._login_mgr.is_logged_in
        return result

    def _find_password_field(self):
        return self._login_mgr._find_password_field()

    def _get_visible_text_inputs(self):
        return self._login_mgr._get_visible_text_inputs()

    def _find_visible_button(self, texts: list):
        return self._login_mgr._find_visible_button(texts)

    def _find_element_multi(self, selectors: list, timeout: int = 10):
        return self._login_mgr.find_element_multi(selectors, timeout)

    def _solve_captcha_with_fallback(self, captcha_solver):
        return self._login_mgr._solve_captcha_with_fallback(captcha_solver)

    def register(self, email: str, password: str, first_name: str, last_name: str,
                 phone: str) -> bool:
        return self._login_mgr.register(email, password, first_name, last_name, phone)

    # ═══════════════════════════════════════════════════════════════════════
    # Business Logic — appointment checking, form filling, booking
    # These remain in this file due to deeply interwoven JS templates.
    # ═══════════════════════════════════════════════════════════════════════

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

                """Acik listeden secim yapar - TEK ATOMIK JS CAGRISI (50+ round-trip yerine 1)"""

                try:

                    search_norm = normalize_tr(search_value)

                    result = self.driver.execute_script("""

                        var searchNorm = arguments[0];

                        

                        function normTr(t) {

                            if(!t) return '';

                            return t.replace(/İ/g,'i').replace(/I/g,'ı').toLowerCase().trim();

                        }

                        

                        var sb = document.querySelector('.select2-search__field');

                        if(sb) {

                            sb.value = arguments[1];

                            sb.dispatchEvent(new Event('input', {bubbles:true}));

                            sb.dispatchEvent(new Event('keyup', {bubbles:true}));

                        }

                        

                        var opts = document.querySelectorAll('.select2-results__option:not(.select2-results__option--disabled)');

                        

                        for(var i=0; i<opts.length; i++) {

                            if(opts[i].offsetWidth <= 0) continue;

                            if(normTr(opts[i].textContent) === searchNorm) {

                                opts[i].scrollIntoView({block:'nearest'});

                                opts[i].click();

                                return 'EXACT:' + opts[i].textContent.trim();

                            }

                        }

                        

                        for(var i=0; i<opts.length; i++) {

                            if(opts[i].offsetWidth <= 0) continue;

                            if(normTr(opts[i].textContent).indexOf(searchNorm) !== -1) {

                                opts[i].scrollIntoView({block:'nearest'});

                                opts[i].click();

                                return 'PARTIAL:' + opts[i].textContent.trim();

                            }

                        }

                        

                        var allLis = document.querySelectorAll('li');

                        for(var i=0; i<allLis.length; i++) {

                            if(allLis[i].offsetWidth <= 0 || !allLis[i].textContent.trim()) continue;

                            if(normTr(allLis[i].textContent).indexOf(searchNorm) !== -1) {

                                allLis[i].scrollIntoView({block:'nearest'});

                                allLis[i].click();

                                return 'FALLBACK:' + allLis[i].textContent.trim();

                            }

                        }

                        

                        return null;

                    """, search_norm, search_value)

                    

                    if result:

                        logger.info(f"  \u2713 {step_name}: {result}")

                        return True

                    

                    logger.warning(f"  {step_name}: '{search_value}' bulunamadi.")

                    return False

                except Exception as e:

                    logger.debug(f"  select2_pick error: {e}")

                return False



            def click_container_by_keywords(keywords: list) -> bool:

                """

                Label'a en yakin dropdown container'i bulur ve tiklar.

                TEK ATOMIK JS CAGRISI - 50+ Selenium round-trip yerine 1 cagri.

                """

                try:

                    result = self.driver.execute_script("""

                        var keywords = arguments[0];

                        

                        var labels = document.querySelectorAll('label');

                        var target = null;

                        for(var i=0; i<labels.length; i++) {

                            if(labels[i].offsetWidth <= 0) continue;

                            var txt = labels[i].textContent.toLowerCase();

                            for(var k=0; k<keywords.length; k++) {

                                if(txt.indexOf(keywords[k].toLowerCase()) !== -1) {

                                    target = labels[i];

                                    break;

                                }

                            }

                            if(target) break;

                        }

                        

                        if(!target) return 'LABEL_NOT_FOUND';

                        

                        var lY = target.getBoundingClientRect().top + window.scrollY;

                        

                        var selectors = [

                            '.select2-selection', '.select2-selection--single',

                            'span.select2-container', 'div.select2-container',

                            '.k-dropdown', '.k-input', 'select'

                        ];

                        

                        var best = null;

                        var bestDist = 999;

                        

                        for(var s=0; s<selectors.length; s++) {

                            var els = document.querySelectorAll(selectors[s]);

                            for(var j=0; j<els.length; j++) {

                                var el = els[j];

                                if(el.offsetWidth <= 0) continue;

                                var eY = el.getBoundingClientRect().top + window.scrollY;

                                var dist = eY - lY;

                                if(dist >= -20 && dist < 300 && dist < bestDist) {

                                    bestDist = dist;

                                    best = el;

                                }

                            }

                        }

                        

                        if(!best) {

                            target.scrollIntoView({block:'center', behavior:'instant'});

                            var rect = target.getBoundingClientRect();

                            var offsets = [15, 25, 35, 45];

                            for(var o=0; o<offsets.length; o++) {

                                var el = document.elementFromPoint(rect.x + 20, rect.y + rect.height + offsets[o]);

                                if(el && el.tagName !== 'HTML' && el.tagName !== 'BODY' && el.tagName !== 'FORM') {

                                    best = el;

                                    break;

                                }

                            }

                        }

                        

                        if(!best) return 'CONTAINER_NOT_FOUND';

                        

                        best.scrollIntoView({block:'center', behavior:'instant'});

                        if(typeof $ !== 'undefined') { $(best).trigger('click'); }

                        else { best.click(); }

                        

                        return 'OK:' + bestDist + 'px';

                    """, keywords)

                    

                    if result and str(result).startswith('OK'):

                        logger.info(f"  Container tiklandi ({result})")

                        return True

                    

                    if result == 'LABEL_NOT_FOUND':

                        logger.warning(f"  Label bulunamadi: {keywords}")

                    elif result == 'CONTAINER_NOT_FOUND':

                        logger.warning(f"  Container bulunamadi (Label: {keywords[0]})")

                    

                    return False

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
                    for attempt in range(3): # 3 deneme (yaklaşık 3s)
                        if click_container_by_keywords(keywords):
                            time.sleep(0.3)
                            if select2_pick(val, name):
                                return True
                        logger.warning(f"  ⏳ {name} açılması bekleniyor... (Deneme {attempt+1}/3)")
                        time.sleep(0.7)
                    
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
                time.sleep(0.15)
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
                            time.sleep(0.7)
                    except: 
                        time.sleep(0.7)
                time.sleep(0.1)
                # C) Location (Opsiyonel / Dinamik)
                if location:
                    logger.info(f"  → Location: {location}")
                    if not _try_pick(["Location", "Konum"], location, "Location", mandatory=True):
                        if form_attempt < max_form_retries - 1: continue
                        else: return False
                    time.sleep(0.15)
                # D) Visa Type
                logger.info(f"  → Visa Type: {visa_type}")
                if not _try_pick(["Visa Type", "Vize Türü"], visa_type, "Visa Type", mandatory=True):
                    if form_attempt < max_form_retries - 1: continue
                    else: return False
                time.sleep(0.15)
                # E) Visa Sub Type (Opsiyonel)
                if visa_sub_type:
                    logger.info(f"  → Sub Type: {visa_sub_type}")
                    _try_pick(["Sub Type", "Alt Tür"], visa_sub_type, "Sub Type", mandatory=False)
                    time.sleep(0.15)
                # F) Category
                if category:
                    logger.info(f"  → Category: {category}")
                    _try_pick(["Category", "Kategori"], category, "Category", mandatory=False)
                    time.sleep(0.3)
                    self._handle_premium_popup()
                    time.sleep(0.3)
                # Submit
                btn = self._find_visible_button(["Submit", "submit", "Ara", "Search"])
                if btn:
                    logger.info("  Submit ediliyor...")
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1.5)
                
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

            # YENİ FIX: "ApplicantSelection in current_url" ise takvimi atla MANTIĞI SİLİNDİ.
            # BLS Türkiye'de Takvim VE Başvuru Formu AYNI sayfada olduğundan, takvim HER ZAMAN işlenmelidir,
            # sayfa URL'si ne olursa olsun. (Özellikle Pending silindikten sonra direkt form geldiğinde atlanmaması için)
            
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
            
            # ⚠️ ROOT CAUSE FIX: Bu log satırı ve altındaki TÜM takvim etkileşimi
            # DAHA ÖNCE `if "(" in str(target_date):` bloğunun İÇİNDE idi!
            # target_date "DD/MM/YYYY" formatında olduğunda (parantez yok) TÜM takvim
            # adımları atlanıyor ve bot direkt Submit'e basıyordu.
            # Şimdi bu blok her zaman çalışacak.
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
                    # ⚠️ ROOT CAUSE FIX: _find_element_multi() gizli elementleri ATLAR (is_displayed() kontrolü)!
                    # Kendo UI file input'u GİZLİ olduğu için _find_element_multi HER ZAMAN None döndürüyor
                    # ve tüm Base64 injection bloğu sessizce ES GEÇİLİYOR.
                    # Çözüm: Element varlığını doğrudan JavaScript ile kontrol et, visibility önemsiz.
                    self._log(logging.INFO, "  Stock vesikalık fotoğraf yükleniyor...")
                    has_file_input = self.driver.execute_script("""
                        return !!(document.getElementById('uploadfile-1') || document.getElementById('PassportCopy') || document.querySelector("input[type='file']"));
                    """)
                    
                    if has_file_input:
                        import os
                        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        log_dir = os.path.join(base_dir, "logs")
                        os.makedirs(log_dir, exist_ok=True)
                        stock_photo_path = os.path.join(log_dir, "test_photo.jpg")
                        
                        # Kullanıcının 200KB altı, tam siyah düzgün bir fotoğraf talebi (Pillow ile)
                        try:
                            from PIL import Image
                            # 400x400 Siyah bir arkaplan oluştur (JPEG olarak genelde 10-20 KB arası tutar)
                            img = Image.new('RGB', (400, 400), color='black')
                            img.save(stock_photo_path, format='JPEG', quality=85)
                        except Exception as e:
                            self._log(logging.WARNING, f"  [APPLICANT] Siyah fotoğraf üretilirken hata: {e}")

                        # DİKKAT: Selenium hidden inputlarda ve Kendo UI'da patlıyor. 
                        # Subagent'ın canlı yayında çözdüğü "Pure Javascript + Base64 DataTransfer" bypass yöntemini kullanacağız.
                        self._log(logging.INFO, f"  [APPLICANT] Selenium es geçiliyor. Base64 JS Injection ile fotoğraf yükleniyor...")
                        
                        try:
                            import base64
                            with open(stock_photo_path, "rb") as f:
                                base64_data = base64.b64encode(f.read()).decode('utf-8')
                                
                            js_upload_trigger = """
                                const base64Content = arguments[0];
                                const fileName = "test_photo.jpg";
                                const mimeType = "image/jpeg";

                                function base64ToBlob(base64, type) {
                                    const binStr = atob(base64);
                                    const len = binStr.length;
                                    const arr = new Uint8Array(len);
                                    for (let i = 0; i < len; i++) {
                                        arr[i] = binStr.charCodeAt(i);
                                    }
                                    return new Blob([arr], { type: type });
                                }

                                const blob = base64ToBlob(base64Content, mimeType);
                                const file = new File([blob], fileName, { type: mimeType });
                                const container = new DataTransfer();
                                container.items.add(file);
                                
                                const input = document.getElementById('uploadfile-1');
                                if (!input) {
                                    return "ERROR: Input #uploadfile-1 not found";
                                }
                                
                                // Inject the file into the input
                                input.files = container.files;
                                
                                // Explicitly trigger the site's upload logic
                                if (typeof onFileChange === 'function') {
                                    onFileChange();
                                } else {
                                    input.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                return "INJECTED";
                            """
                            
                            inject_result = self.driver.execute_script(js_upload_trigger, base64_data)
                            self._log(logging.INFO, f"  [APPLICANT] JS Injection Sonucu: {inject_result}")
                            
                            # Yükleme sonrası "Confirm Photo" pop-up'ını yakalama ve Onaylama
                            self._log(logging.INFO, "  [APPLICANT] 'Confirm Photo' onay pop-up'ı bekleniyor ve 'Understood' tıklanıyor...")
                            time.sleep(2) # AJAX POST'un tamamlanması için kısa bekleme
                            
                            js_confirm_photo = """
                                // Callback tetikle
                                if (typeof OnPhotoAccepted === 'function') {
                                    OnPhotoAccepted();
                                }
                                
                                // Butona da basalım garanti olsun
                                var btn = document.querySelector("#photoUploadModal button[onclick*='OnPhotoAccepted']");
                                if(btn) { btn.click(); }
                                else {
                                    var btns = document.querySelectorAll('#photoUploadModal button');
                                    for(var i=0; i<btns.length; i++) {
                                        if(btns[i].innerText.toLowerCase().includes('understood') || btns[i].innerText.toLowerCase().includes('anladım')) {
                                            btns[i].click();
                                            break;
                                        }
                                    }
                                }
                                
                                // Modal kapatmayı zorla
                                if (typeof $ !== 'undefined' && $('#photoUploadModal').length) {
                                    try { $('#photoUploadModal').modal('hide'); } catch(e) {}
                                    $('#photoUploadModal').removeClass('show').css('display', 'none');
                                    $('.modal-backdrop').remove();
                                    $('body').removeClass('modal-open').css('padding-right', '');
                                }
                                
                                // Final check: id gelmiş mi?
                                return document.getElementById('ApplicantPhotoId') ? document.getElementById('ApplicantPhotoId').value : null;
                            """
                            
                            photo_id = self.driver.execute_script(js_confirm_photo)
                            self._log(logging.INFO, f"  [APPLICANT] Fotoğraf onayı tamamlandı. Sistemdeki Photo ID'si: {photo_id or 'Okunamadı'}")
                            
                        except Exception as e:
                            self._log(logging.ERROR, f"  [APPLICANT] JS Injection kritik hata: {e}")
                            
                        # AJAX'ın dinlenmesi için biraz daha süre
                        time.sleep(2)
                    else:
                        self._log(logging.WARNING, "  [APPLICANT] ⚠️ Sayfada hiçbir file input bulunamadı! Fotoğraf yükleme ATLANIYOR.")
                    
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

                            reader = OTPReader(email_address, app_password, log_func=otp_log_hook, target_email=self.user_data.get("email"))
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
                    self._log(logging.INFO, "  >> Submit/Proceed (Applicant Selection) butonuna basılıyor.")
                    
                    # Element is not clickable at point (overlay) hatasını önlemek için %100 Javascript kullanıyoruz:
                    js_final_submit = """
                        var submitBtn = document.getElementById('btnSubmit') || document.querySelector('input[value="Submit"]') || document.querySelector('button[type="submit"]');
                        if(submitBtn) {
                            submitBtn.scrollIntoView({block: 'center', behavior: 'instant'});
                            submitBtn.click();
                            return true;
                        }
                        return false;
                    """
                    success_submit = self.driver.execute_script(js_final_submit)
                    if not success_submit:
                        self._log(logging.WARNING, "  [APPLICANT] Submit butonu bulunamadı, form direkt post ediliyor...")
                        self.driver.execute_script("try{ document.forms[0].submit(); }catch(e){}")
                        
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
