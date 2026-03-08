import sys
import os
import subprocess
import threading
import uvicorn
import logging

# Ensure the root directory is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gui.dashboard import DashboardWindow
from bot.telemetry import start_telemetry_server
from api.main import app as fastapi_app

def start_api_server(bot_manager):
    """Starts the FastAPI Uvicorn server in a background thread and binds the bot_manager"""
    # Inject the singleton bot_manager into the FastAPI application state
    fastapi_app.state.bot_manager = bot_manager
    logging.info("Starting REST API Server on port 8000...")
    # NOTE: run uvicorn programmatically
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run(fastapi_app, host=api_host, port=api_port, log_config=None)

def start_frontend():
    """Starts the Vite React/Vue frontend server automatically on port 5173"""
    logging.info("Starting Web Frontend (Vite) on port 5173...")
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_panel")
    if os.path.exists(frontend_dir):
        try:
            # shell=True is needed on Windows to resolve 'npm'
            subprocess.Popen(
                "npm run dev", 
                cwd=frontend_dir, 
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP # Windows specific to allow background detach
            )
        except Exception as e:
            logging.error(f"Failed to start frontend: {e}")
    else:
        logging.warning(f"Frontend directory not found at {frontend_dir}")

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

if __name__ == "__main__":
    cleanup_zombie_processes()
    
    start_telemetry_server(port=9090) # Switched telemetry back to 9090 to avoid 8000 conflict
    
    # Start the Tkinter App
    app = DashboardWindow()
    
    # Start the React/Vue Frontend UI automatically
    start_frontend()
    
    # Start the FastAPI Server in a background thread, sharing the exact same manager
    api_thread = threading.Thread(target=start_api_server, args=(app.manager,), daemon=True)
    api_thread.start()
    
    app.mainloop()
