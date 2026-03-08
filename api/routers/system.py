from fastapi import APIRouter, Request
from data.repositories import GlobalSettingsRepository
from config.cache import redis_manager
import time
import logging

router = APIRouter()

@router.get("/settings", summary="Get application global settings")
def get_global_settings():
    """Retrieve all configurable settings like active_hours, captcha keys, etc."""
    return {"settings": GlobalSettingsRepository.get_all()}

@router.get("/telemetry", summary="Get live system telemetry metrics")
def get_telemetry(request: Request):
    """Returns real-time system metrics (useful for the web dashboard)."""
    import psutil
    mem = psutil.virtual_memory()
    
    metrics = {
        "timestamp": time.time(),
        "healthy_proxies": 0,
        "cooldown_proxies": 0,
        "active_workers": 0,
        "max_workers": 15,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "bot_ram_mb": 0,
    }
    
    # Calculate total RAM used by bot Chrome processes (NOT personal browser)
    try:
        bot_ram = 0
        # First find all chromedriver PIDs
        driver_pids = set()
        for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
            name = (proc.info['name'] or '').lower()
            if name in ('undetected_chromedriver.exe', 'chromedriver.exe'):
                driver_pids.add(proc.info['pid'])
                bot_ram += proc.info['memory_info'].rss
        # Then count only chrome.exe that are children of chromedriver
        for proc in psutil.process_iter(['pid', 'name', 'ppid', 'cmdline', 'memory_info']):
            name = (proc.info['name'] or '').lower()
            if name == 'chrome.exe':
                cmd = ' '.join(proc.info.get('cmdline') or [])
                is_bot = proc.info['ppid'] in driver_pids or '--headless' in cmd or '--remote-debugging-pipe' in cmd
                if is_bot:
                    bot_ram += proc.info['memory_info'].rss
        metrics["bot_ram_mb"] = round(bot_ram / (1024**2))
    except Exception:
        pass

    # Worker cap metrics from BotManager
    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager:
        metrics["active_workers"] = bot_manager.active_worker_count
        metrics["max_workers"] = bot_manager._max_workers

    r = redis_manager.get_client()
    if redis_manager.is_connected and r:
        try:
            metrics["healthy_proxies"] = r.scard("Proxy:ActiveList")
            metrics["cooldown_proxies"] = r.zcard("Proxy:CooldownQueue")
        except Exception:
            pass
            
    return metrics

@router.post("/test_notification", summary="Send a test appointment notification")
def test_notification(request: Request):
    """Simulates finding an appointment and triggers all notification channels."""
    import logging, json
    from data.repositories import GlobalSettingsRepository
    
    config = GlobalSettingsRepository.get_all()
    test_dates = "27.02.2026, 28.02.2026 (TEST - Premium)"
    results = {"discord": False, "callmebot": False, "telegram": False, "log": False}
    
    # 1. Log to BotManager's log fan-out (appears in System Logs panel)
    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager:
        bot_manager._sys_log(logging.INFO, f"🎉 [TEST] RANDEVU BULUNDU! Tarihler: {test_dates}")
        results["log"] = True
    
    # 2. Discord Webhook
    try:
        discord_wh = config.get("discord_webhook", "")
        if discord_wh:
            from bot.notifier import DiscordNotifier
            msg = f"🧪 **TEST BİLDİRİMİ**\n🎉 **RANDEVU BULUNDU!**\n📅 Tarihler: {test_dates}\n\n_Bu bir test bildirimidir, gerçek randevu değildir._"
            DiscordNotifier(discord_wh).send_message(msg)
            results["discord"] = True
    except Exception as e:
        results["discord"] = str(e)
    
    # 3. Telegram Bot (direct HTTP API — daemon bağımsız)
    try:
        bot_token = config.get("telegram_bot_token", "").strip()
        admin_ids_raw = config.get("telegram_admin_id", "").strip()
        if bot_token and admin_ids_raw:
            import urllib.request
            admin_ids = [x.strip() for x in admin_ids_raw.split(",") if x.strip()]
            alert_msg = f"🧪 *TEST BİLDİRİMİ*\n🎉 *RANDEVU BULUNDU!*\n📅 {test_dates}\n\n_Bu bir test bildirimidir._"
            sent_count = 0
            for aid in admin_ids:
                try:
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    payload = json.dumps({"chat_id": aid, "text": alert_msg, "parse_mode": "Markdown"}).encode()
                    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    sent_count += 1
                except Exception as te:
                    results["telegram"] = f"ID {aid}: {str(te)[:80]}"
            if sent_count > 0:
                results["telegram"] = True
        else:
            results["telegram"] = False  # Token veya admin ID yok
    except Exception as e:
        results["telegram"] = str(e)
    
    return {"status": "success", "message": "Test notification sent", "results": results}

from api.models import GlobalSettingUpdate, GlobalSettingsBulkUpdate

@router.post("/settings", summary="Update a single global setting")
def update_global_setting(setting: GlobalSettingUpdate):
    """Updates or inserts a key-value pair in the global settings."""
    GlobalSettingsRepository.set(setting.key, setting.value)
    return {"status": "success", "message": f"Setting {setting.key} updated."}

@router.post("/settings/bulk", summary="Bulk update global settings")
def update_global_settings_bulk(bulk_update: GlobalSettingsBulkUpdate):
    """Updates multiple global settings at once."""
    for key, value in bulk_update.settings.items():
        GlobalSettingsRepository.set(key, value)
    return {"status": "success", "message": "Bulk settings updated successfully."}

from fastapi import WebSocket, WebSocketDisconnect
import asyncio

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """Streams logs via non-destructive LogFanOut (P3: multiple consumers can read independently)."""
    await websocket.accept()
    bot_manager = getattr(websocket.app.state, "bot_manager", None)
    
    if not bot_manager:
        await websocket.send_json({"error": "BotManager not initialized"})
        await websocket.close()
        return
    
    # Send an initial heartbeat so the frontend knows the connection works
    await websocket.send_json({"logs": [], "connected": True})
    
    last_seq = bot_manager.log_fan_out.latest_seq  # Start from current position
    
    try:
        while True:
            try:
                new_seq, records = bot_manager.log_fan_out.read_since(last_seq, limit=50)
                if records:
                    logs = []
                    for record in records:
                        log_entry = {
                            "time": record.created,
                            "level": record.levelname,
                            "message": record.getMessage()
                        }
                        logs.append(log_entry)
                    await websocket.send_json({"logs": logs})
                    last_seq = new_seq
            except WebSocketDisconnect:
                break
            except Exception as e:
                import traceback
                logging.error(f"WebSocket log streaming error: {e}")
                traceback.print_exc()
                
            await asyncio.sleep(0.5) # Poll every 500ms
            
    except WebSocketDisconnect:
        pass

import os
import json
from fastapi import HTTPException

@router.get("/logs/{user_id}", summary="Get logs for a specific user ID")
def get_user_logs(user_id: int, limit: int = 100):
    """Retrieve the most recent parsed JSON logs for a specific worker."""
    log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs", "telemetry_metrics.json")
    
    if not os.path.exists(log_file):
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f: pass
        return {"logs": []}
        
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if str(data.get("user_id", "")) == str(user_id):
                        # Map internal telemetry log keys to standard view keys
                        log_entry = {
                            "time": data.get("timestamp", 0),
                            "level": data.get("level", "INFO"),
                            "message": data.get("message", "")
                        }
                        logs.append(log_entry)
                except json.JSONDecodeError:
                    continue
        return {"logs": logs[-limit:]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
