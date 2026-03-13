import os
import zipfile
import tempfile
import uuid


def create_proxy_extension(proxy_string: str) -> str:
    """
    KullaniciAdi:Sifre@IP:Port formatindaki proxy'yi Selenium'a (ChromeDriver)
    ekleyebilmek icin gecici bir Chrome eklentisi uretir.

    Chrome 130+ icin Manifest V3 formatini kullanir.
    Dönen zip dosyasinin yolunu uc.Chrome(options) icine driver.add_extension() ile ekleriz.
    """
    try:
        if "@" not in proxy_string:
            return ""  # Auth yoksa normal IP:Port veya hatalidir

        credentials, endpoint = proxy_string.split("@")
        username, password = credentials.split(":")
        host, port = endpoint.split(":")

        # ═══════════════════════════════════════════════════════════════
        # Manifest V3 — Chrome 130+ uyumlu
        # ═══════════════════════════════════════════════════════════════
        manifest_json = """{
    "version": "1.0.0",
    "manifest_version": 3,
    "name": "BLS Bot Proxy",
    "permissions": [
        "proxy",
        "webRequest",
        "webRequestAuthProvider"
    ],
    "host_permissions": [
        "<all_urls>"
    ],
    "background": {
        "service_worker": "background.js"
    },
    "minimum_chrome_version": "130.0.0"
}"""

        # MV3 service worker: proxy config + auth handler
        # chrome.webRequest.onAuthRequired still works in MV3 but needs
        # "webRequestAuthProvider" permission and async callback pattern.
        background_js = f"""
// --- Proxy Configuration ---
chrome.proxy.settings.set(
  {{
    value: {{
      mode: "fixed_servers",
      rules: {{
        singleProxy: {{
          scheme: "http",
          host: "{host}",
          port: parseInt({port})
        }},
        bypassList: ["localhost", "127.0.0.1"]
      }}
    }},
    scope: "regular"
  }},
  function() {{}}
);

// --- Proxy Authentication (MV3 compatible) ---
chrome.webRequest.onAuthRequired.addListener(
  function(details, callbackFn) {{
    callbackFn({{
      authCredentials: {{
        username: "{username}",
        password: "{password}"
      }}
    }});
  }},
  {{ urls: ["<all_urls>"] }},
  ["asyncBlocking"]
);
"""

        # Temp klasöründe benzersiz bir zip dosyasi olustur
        ext_folder = os.path.join(
            tempfile.gettempdir(), f"bs_proxy_ext_{uuid.uuid4().hex}"
        )
        os.makedirs(ext_folder, exist_ok=True)

        zip_path = ext_folder + ".zip"

        with zipfile.ZipFile(zip_path, "w") as zp:
            zp.writestr("manifest.json", manifest_json)
            zp.writestr("background.js", background_js)

        return zip_path

    except Exception as e:
        print(f"Proxy Eklentisi olusturulurken hata: {e}")
        return ""
