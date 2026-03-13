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
        self._proxy_relay = None  # ProxyRelay instance for authenticated proxies

    def _log(self, level, msg):
        if self.log_func:
            self.log_func(level, msg)
        else:
            logger.log(level, f"[{self.user_data.get('first_name', '?')}] {msg}")

    def generate_fingerprint(self):
        """Generates a consistent, semi-random fingerprint based on user_id.
        Uses a thread-local Random instance to avoid corrupting the global PRNG.
        Chrome version range updated to 134-146 to match modern Chrome installs."""
        rng = random.Random(str(self.user_data.get('id', '0')))
        major_version = rng.randint(134, 146)
        minor = rng.randint(0, 9)
        build = rng.randint(5000, 7700)
        patch = rng.randint(0, 200)

        os_options = [
            "Windows NT 10.0; Win64; x64",
            "Windows NT 10.0; Win64; x64",  # Win10 is most common
        ]
        os_ver = rng.choice(os_options)
        ua = f"Mozilla/5.0 ({os_ver}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major_version}.0.{build}.{patch} Safari/537.36"

        resolutions = ["1366,768", "1440,900", "1600,900", "1920,1080"]
        res = rng.choice(resolutions)
        return ua, res

    def create_driver(self):
        """Launch Chrome with stealth, fingerprinting, and optional proxy extension.
        Returns True on success, False on failure."""
        try:
            ua, res = self.generate_fingerprint()
            
            # Persistent Profile Session Dir
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            profile_dir = os.path.join(base_dir, "data", "profiles", str(self.user_data.get("id", "default")))
            os.makedirs(profile_dir, exist_ok=True)

            if self.headless:
                self._log(logging.INFO, f"Gizli (Stealth Headless) modda başlatılıyor... [UA: Chrome {ua.split('Chrome/')[1].split(' ')[0]}]")
                import undetected_chromedriver as uc
                import re

                max_retries = 3

                # Proactively detect installed Chrome version
                version_main = None
                try:
                    import subprocess
                    reg_paths = [
                        r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon',
                        r'HKEY_LOCAL_MACHINE\SOFTWARE\Google\Chrome\BLBeacon',
                    ]
                    for reg_path in reg_paths:
                        try:
                            reg_result = subprocess.run(
                                ['reg', 'query', reg_path, '/v', 'version'],
                                capture_output=True, text=True, timeout=5
                            )
                            match = re.search(r'(\d+)\.', reg_result.stdout)
                            if match:
                                version_main = int(match.group(1))
                                self._log(logging.INFO, f"Chrome v{version_main} algılandı ({reg_path}), uyumlu driver kullanılacak.")
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

                for attempt in range(max_retries):
                    options = uc.ChromeOptions()
                    options.add_argument("--no-sandbox")
                    options.add_argument("--disable-dev-shm-usage")
                    options.add_argument(f"--window-size={res}")
                    options.add_argument("--disable-background-networking")
                    options.add_argument("--disable-default-apps")
                    options.add_argument("--mute-audio")
                    options.add_argument("--js-flags=--max-old-space-size=256")

                    # Anti-leak: WebRTC IP leak prevention + DNS leak prevention
                    options.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")
                    options.add_argument("--enforce-webrtc-ip-permission-check")
                    options.add_argument("--disable-features=WebRtcHideLocalIpsWithMdns")

                    if self.proxy:
                        if "@" in self.proxy:
                            # Headless Chrome does NOT load extensions!
                            # Use local ProxyRelay: 127.0.0.1:PORT → upstream auth proxy
                            from bot.proxy_relay import ProxyRelay
                            if not self._proxy_relay:
                                self._proxy_relay = ProxyRelay(self.proxy)
                                self._proxy_relay.start()
                            options.add_argument(f"--proxy-server=http://{self._proxy_relay.local_address}")
                            if attempt == 0:
                                self._log(logging.INFO, f"Proxy Relay aktif: 127.0.0.1:{self._proxy_relay.local_port} → {self.proxy.split('@')[1]}")
                        else:
                            options.add_argument(f"--proxy-server={self.proxy}")
                            if attempt == 0:
                                self._log(logging.INFO, f"Proxy Aktif: {self.proxy}")

                    options.add_argument(f"user-agent={ua}")
                    options.add_argument("--disable-gpu")
                    options.add_argument("--enable-javascript")

                    try:
                        kwargs = {"options": options, "headless": True, "use_subprocess": True, "user_data_dir": profile_dir}
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
                        # Non-headless can use extensions, but ProxyRelay is more reliable
                        from bot.proxy_relay import ProxyRelay
                        if not self._proxy_relay:
                            self._proxy_relay = ProxyRelay(self.proxy)
                            self._proxy_relay.start()
                        options.add_argument(f"--proxy-server=http://{self._proxy_relay.local_address}")
                        self._log(logging.INFO, f"Proxy Relay aktif: 127.0.0.1:{self._proxy_relay.local_port} → {self.proxy.split('@')[1]}")
                    else:
                        options.add_argument(f"--proxy-server={self.proxy}")
                        self._log(logging.INFO, f"Proxy Aktif: {self.proxy}")
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option("useAutomationExtension", False)
                options.add_argument(f"--window-size={res}")
                options.add_argument(f"--user-data-dir={profile_dir}")
                options.add_argument(f"user-agent={ua}")
                self.driver = webdriver.Chrome(options=options)

            # ═══════════════════════════════════════════════════════════
            # CDP Timezone & Geolocation Override (Match Turkish proxy)
            # ═══════════════════════════════════════════════════════════
            try:
                self.driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {
                    "timezoneId": "Europe/Istanbul"
                })
                self.driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
                    "latitude": 41.0082,
                    "longitude": 28.9784,
                    "accuracy": 100
                })
                self._log(logging.INFO, "Timezone → Europe/Istanbul, Geolocation → İstanbul")
            except Exception as tz_err:
                self._log(logging.WARNING, f"CDP timezone/geoloc override hatası: {tz_err}")

            # Anti-detection JS (comprehensive for Chrome 134+)
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            width, height = res.split(',')
            self.driver.execute_script(f"""
                Object.defineProperty(window.screen, 'width', {{get: () => {width}}});
                Object.defineProperty(window.screen, 'height', {{get: () => {height}}});
                Object.defineProperty(window.screen, 'availWidth', {{get: () => {width}}});
                Object.defineProperty(window.screen, 'availHeight', {{get: () => {height}}});
                Object.defineProperty(navigator, 'maxTouchPoints', {{get: () => 0}});
                Object.defineProperty(navigator, 'languages', {{get: () => ['tr-TR', 'tr', 'en-US', 'en']}});
                Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => 4}});
                Object.defineProperty(navigator, 'deviceMemory', {{get: () => 8}});
                // Headless detection: override permissions query
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({{ state: Notification.permission }}) :
                    originalQuery(parameters)
                );
                // Override connection info to look like ethernet
                if (navigator.connection) {{
                    Object.defineProperty(navigator.connection, 'rtt', {{get: () => 50}});
                }}
            """)

            # Selenium Stealth
            try:
                from selenium_stealth import stealth
                renderer = random.choice([
                    "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (Intel, Intel(R) HD Graphics 530 Direct3D11 vs_5_0 ps_5_0)",
                ])
                stealth(self.driver,
                        languages=["tr-TR", "tr", "en-US", "en"],
                        vendor="Google Inc.",
                        platform="Win32",
                        webgl_vendor="Intel Inc.",
                        renderer=renderer,
                        fix_hairline=True)
                self._log(logging.INFO, "Stealth JS Enjekte Edildi.")
            except ImportError:
                self._log(logging.WARNING, "selenium-stealth kütüphanesi bulunamadı, standart ayarlar ile devam ediliyor.")

            self.wait = WebDriverWait(self.driver, 20)
            self._log(logging.INFO, "Chrome WebDriver başlatıldı (Anti-Detection Aktif)")
            return True
        except Exception as e:
            self._log(logging.ERROR, f"WebDriver başlatma hatası: {e}")
            return False

    def close_driver(self):
        """Quit the WebDriver, release proxy slot, and stop ProxyRelay."""
        if self.driver:
            try:
                self.driver.quit()
                from bot.proxy_manager import proxy_manager
                proxy_manager.report_release(self.proxy)
            except Exception:
                pass
            finally:
                self.driver = None
        # Stop the local proxy relay if running
        if self._proxy_relay:
            try:
                self._proxy_relay.stop()
            except Exception:
                pass
            self._proxy_relay = None
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
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "network_activity.json")
            
            with open(log_file_path, "w", encoding="utf-8") as f:
                json.dump(filtered_logs, f, indent=2)
            logger.info(f"✅ Network logları kaydedildi: {log_file_path} ({len(filtered_logs)} olay)")
        except Exception as e:
            logger.error(f"Log dump hatası: {e}")
