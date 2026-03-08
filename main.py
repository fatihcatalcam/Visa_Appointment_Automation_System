import sys
import os
import subprocess
import uvicorn
import logging

# Ensure the root directory is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot.telemetry import start_telemetry_server

def cleanup_zombie_processes():
    """Kills leftover chrome and chromedriver processes to prevent memory leaks, but strictly targets only bot-created instances."""
    logging.info("Sürekli performans için zombi Chrome işlemleri temizleniyor...")
    try:
        if os.name == 'nt':
            # Sadece ChromeDriver.exe'leri tamamen öldür
            subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Kullanıcının KENDİ chrome tarayıcısını kapatmamak için, sadece bot parametreleriyle açılmış Chrome'ları bul ve öldür
            ps_script = 'Get-WmiObject Win32_Process -Filter "Name=\'chrome.exe\'" | Where-Object { $_.CommandLine -match "remote-debugging" -or $_.CommandLine -match "scoped_dir" -or $_.CommandLine -match "headless" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'
            subprocess.run(["powershell", "-Command", ps_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.warning(f"Zombi temizleme hatası: {e}")

def ensure_api_key():
    from data.repositories import GlobalSettingsRepository
    import secrets
    import string
    current_key = GlobalSettingsRepository.get("api_key", "").strip()
    if not current_key:
        alphabet = string.ascii_letters + string.digits
        new_key = ''.join(secrets.choice(alphabet) for i in range(16))
        GlobalSettingsRepository.set("api_key", new_key)
        logging.warning("="*60)
        logging.warning("🔐 YENI WEB PANEL PAROLASI OLUSTURULDU 🔐")
        logging.warning(f"👉 PAROLA: {new_key}")
        logging.warning("Lutfen bu parolayi kopyalayip web paneline giris yapin.")
        logging.warning("="*60)
    else:
        logging.info("🔒 Web Panel Güvenliği Aktif. Parola gereklidir.")

if __name__ == "__main__":
    # Setup basic logging to terminal
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("Starting SaaS Bot Control Server (Headless VPS Mode)...")
    
    ensure_api_key()
    cleanup_zombie_processes()
    start_telemetry_server(port=9090) 
    
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_port = int(os.getenv("API_PORT", "8000"))
    
    # We pass the app as an import string, so uvicorn can manage workers/reloading if needed
    # The lifespan in api.main:app will automatically safely instantiate BotManager()
    logging.info(f"Starting API Server on {api_host}:{api_port}")
    uvicorn.run("api.main:app", host=api_host, port=api_port, log_level="info")
