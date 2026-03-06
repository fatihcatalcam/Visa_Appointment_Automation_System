"""
Browser Factory Module — Chrome driver lifecycle management.

Extracted from BLSScraper: start_driver(), stop_driver(), _generate_fingerprint(),
dump_network_logs(), and stealth configuration.
"""
import os
import time
import random
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


class BrowserFactory:
    """Creates and manages Chrome WebDriver instances with stealth and proxy support."""

    def __init__(self, user_data: dict, config: dict = None, log_func=None):
        self.user_data = user_data
        self.config = config or {}
        self.log_func = log_func
        self.headless = bool(user_data.get("headless", True))
        self.proxy = user_data.get("proxy_address", "")
        self.driver = None
        self.wait = None

    def _log(self, level, msg):
        if self.log_func:
            self.log_func(level, msg)
        else:
            logger.log(level, f"[{self.user_data.get('first_name', '?')}] {msg}")

    def generate_fingerprint(self):
        """Generates a consistent, semi-random fingerprint based on user_id.
        Uses a thread-local Random instance to avoid corrupting the global PRNG."""
        rng = random.Random(str(self.user_data.get('id', '0')))
        major_version = rng.randint(118, 122)
        minor = rng.randint(0, 9)
        build = rng.randint(1000, 6000)
        patch = rng.randint(0, 150)

        os_options = [
            "Windows NT 10.0; Win64; x64",
            "Windows NT 11.0; Win64; x64"
        ]
        os_ver = rng.choice(os_options)
        ua = f"Mozilla/5.0 ({os_ver}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major_version}.0.{build}.{patch} Safari/537.36"

        resolutions = ["1280,900", "1366,768", "1440,900", "1600,900", "1920,1080"]
        res = rng.choice(resolutions)
        return ua, res

    def create_driver(self):
        """Launch Chrome with stealth, fingerprinting, and optional proxy extension.
        Returns True on success, False on failure."""
        try:
            ua, res = self.generate_fingerprint()

            if self.headless:
                self._log(logging.INFO, f"Gizli (Stealth Headless) modda başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
                import undetected_chromedriver as uc
                import re

                max_retries = 3

                # Proactively detect installed Chrome version
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
                                if attempt == 0:
                                    self._log(logging.INFO, "Auth-Proxy Eklentisi Yüklendi.")
                        else:
                            options.add_argument(f"--proxy-server={self.proxy}")
                            if attempt == 0:
                                self._log(logging.INFO, f"Proxy Aktif: {self.proxy}")

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
                self.driver = webdriver.Chrome(options=options)

            # Anti-detection JS
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            width, height = res.split(',')
            self.driver.execute_script(f"""
                Object.defineProperty(window.screen, 'width', {{get: () => {width}}});
                Object.defineProperty(window.screen, 'height', {{get: () => {height}}});
            """)

            # Selenium Stealth
            try:
                from selenium_stealth import stealth
                vendor = random.choice(["Google Inc.", "Apple Computer, Inc."])
                renderer = random.choice([
                    "Intel Iris OpenGL Engine",
                    "AMD Radeon Pro 5300M OpenGL Engine",
                    "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"
                ])
                stealth(self.driver,
                        languages=["tr-TR", "tr", "en-US", "en"],
                        vendor=vendor,
                        platform="Win32",
                        webgl_vendor="Intel Inc.",
                        renderer=renderer,
                        fix_hairline=True)
                self._log(logging.INFO, "Stealth JS Enjekte Edildi.")
            except ImportError:
                self._log(logging.WARNING, "selenium-stealth kütüphanesi bulunamadı, standart ayarlar ile devam ediliyor.")

            self.wait = WebDriverWait(self.driver, 20)
            self._log(logging.INFO, "Chrome WebDriver başlatıldı (Network Loglama Aktif)")
            return True
        except Exception as e:
            self._log(logging.ERROR, f"WebDriver başlatma hatası: {e}")
            return False

    def close_driver(self):
        """Quit the WebDriver and release proxy slot."""
        if self.driver:
            try:
                self.driver.quit()
                from bot.proxy_manager import proxy_manager
                proxy_manager.report_release(self.proxy)
            except Exception:
                pass
            finally:
                self.driver = None
            self._log(logging.INFO, "WebDriver kapatıldı")

    def dump_network_logs(self):
        """Save performance logs to JSON file for API analysis."""
        try:
            if not self.driver:
                return
            logs = self.driver.get_log("performance")
            import json
            filtered_logs = []
            for entry in logs:
                try:
                    msg = json.loads(entry["message"])["message"]
                    if "Network.requestWillBeSent" in msg["method"] or "Network.responseReceived" in msg["method"]:
                        filtered_logs.append(msg)
                except Exception:
                    pass
            with open("network_activity.json", "w", encoding="utf-8") as f:
                json.dump(filtered_logs, f, indent=2)
            logger.info(f"✅ Network logları kaydedildi: network_activity.json ({len(filtered_logs)} olay)")
        except Exception as e:
            logger.error(f"Log dump hatası: {e}")
