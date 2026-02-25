import os
import zipfile
import tempfile
import uuid

def create_proxy_extension(proxy_string: str) -> str:
    """
    KullaniciAdi:Sifre@IP:Port formatindaki proxy'yi Selenium'a (ChromeDriver)
    ekleyebilmek icin gecici bir Chrome eklentisi uretir.
    Dönen zip dosyasinin yolunu uc.Chrome(options) icine driver.add_extension() ile ekleriz.
    """
    try:
        if "@" not in proxy_string:
            return "" # Auth yoksa normal IP:Port veya hatalidir
        
        credentials, endpoint = proxy_string.split("@")
        username, password = credentials.split(":")
        host, port = endpoint.split(":")
        
        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "BLS Bot Proxy Eklentisi",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """

        background_js = f"""
        var config = {{
                mode: "fixed_servers",
                rules: {{
                  singleProxy: {{
                    scheme: "http",
                    host: "{host}",
                    port: parseInt({port})
                  }},
                  bypassList: ["localhost"]
                }}
              }};

        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

        function callbackFn(details) {{
            return {{
                authCredentials: {{
                    username: "{username}",
                    password: "{password}"
                }}
            }};
        }}

        chrome.webRequest.onAuthRequired.addListener(
            callbackFn,
            {{urls: ["<all_urls>"]}},
            ['blocking']
        );
        """
        
        # Temp klasöründe benzersiz bir zip dosyasi olustur (Her bot ayri thread oldugu icin çakismaz)
        ext_folder = os.path.join(tempfile.gettempdir(), f"bs_proxy_ext_{uuid.uuid4().hex}")
        os.makedirs(ext_folder, exist_ok=True)
        
        zip_path = ext_folder + ".zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zp:
            zp.writestr("manifest.json", manifest_json)
            zp.writestr("background.js", background_js)
            
        return zip_path
        
    except Exception as e:
         print(f"Proxy Eklentisi olusturulurken hata: {e}")
         return ""
