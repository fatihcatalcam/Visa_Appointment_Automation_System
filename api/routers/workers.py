from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from data.repositories import UserRepository
from fastapi import Request

router = APIRouter()

def get_bot_manager(request: Request):
    return request.app.state.bot_manager

@router.get("", summary="Get all workers and their statuses")
def get_all_workers():
    """Returns the list of all users/workers from the DB along with live status."""
    return {"workers": UserRepository.get_all()}

@router.post("/{user_id}/start", summary="Start a single bot worker")
def start_worker(user_id: int, bot_manager = Depends(get_bot_manager)):
    """Wakes up the bot with the given User ID if it is idle or sleeping."""
    user = UserRepository.get_by_id(user_id)
    if not user:
         raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")
    
    bot_manager.start_single(user_id)
    return {"status": "success", "message": f"Start command sent to worker {user_id}"}
    
@router.post("/{user_id}/stop", summary="Stop a single bot worker")
def stop_worker(user_id: int, bot_manager = Depends(get_bot_manager)):
    """Gracefully stops the worker thread assigned to the given User ID."""
    bot_manager.stop_user(user_id)
    return {"status": "success", "message": f"Stop command sent to worker {user_id}"}
    
@router.post("/start_all", summary="Start all active bots")
def start_all_workers(bot_manager = Depends(get_bot_manager)):
    """Triggers staggered boot for all bots marked as is_active=True."""
    bot_manager.start_all()
    return {"status": "success", "message": "Global start command issued."}
    
@router.post("/stop_all", summary="Stop all bots")
def stop_all_workers(bot_manager = Depends(get_bot_manager)):
    """Halts all currently running bots."""
    bot_manager.stop_all()
    return {"status": "success", "message": "Global stop command issued."}

import os
import logging
@router.post("/kill_all", summary="Emergency Kill Switch")
def kill_all_workers(bot_manager = Depends(get_bot_manager)):
    """Force stops all bots and kills only bot-spawned Chrome processes (not personal browser)."""
    bot_manager.stop_all()
    killed = 0
    try:
        import psutil
        # Find all chromedriver PIDs first
        driver_pids = set()
        for proc in psutil.process_iter(['pid', 'name']):
            name = (proc.info['name'] or '').lower()
            if name in ('undetected_chromedriver.exe', 'chromedriver.exe'):
                driver_pids.add(proc.info['pid'])
        
        # Kill chromedriver processes AND all their child chrome.exe processes
        for dpid in driver_pids:
            try:
                parent = psutil.Process(dpid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                        killed += 1
                    except Exception:
                        pass
                parent.kill()
                killed += 1
            except psutil.NoSuchProcess:
                pass
        
        logging.info(f"Emergency Kill: {killed} bot Chrome/driver süreçleri sonlandırıldı. Kişisel tarayıcıya dokunulmadı.")
    except Exception as e:
        logging.error(f"Kill switch hatası: {e}")
        # Fallback: only kill drivers, NOT chrome.exe
        os.system("taskkill /f /im undetected_chromedriver.exe /T >nul 2>&1")
        os.system("taskkill /f /im chromedriver.exe /T >nul 2>&1")
    return {"status": "success", "message": f"Emergency kill: {killed} bot processes terminated."}

from config.database import clear_user_cooldown, clear_all_cooldowns

@router.post("/clear_all_cooldowns", summary="Global Cooldown Reset")
def clear_all_worker_cooldowns():
    """Resets the cooldown state for ALL accounts at once."""
    count = clear_all_cooldowns()
    return {"status": "success", "message": f"Cooldown cleared for {count} users"}

@router.post("/{user_id}/clear_cooldown", summary="Clear Cooldown for a user")
def clear_worker_cooldown(user_id: int):
    """Resets the cooldown constraint on a user/bot."""
    user = UserRepository.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    clear_user_cooldown(user_id)
    return {"status": "success", "message": f"Cooldown cleared for user {user_id}"}


from api.models import UserCreateUpdate
from config.security import _simple_encode

@router.post("", summary="Add a new user/bot")
def create_worker(user_data: UserCreateUpdate):
    """Creates a new user record in the database."""
    data = user_data.model_dump()
    if data.get("password_enc"):
        data["password_enc"] = _simple_encode(data["password_enc"])
    data["status"] = "Idle"
    UserRepository.create(data)
    return {"status": "success", "message": "User added successfully."}

@router.put("/{user_id}", summary="Update a user/bot")
def update_worker(user_id: int, user_data: UserCreateUpdate):
    """Updates an existing user record in the database."""
    if not UserRepository.get_by_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
        
    data = user_data.model_dump(exclude_unset=True)
    if data.get("password_enc"):
        data["password_enc"] = _simple_encode(data["password_enc"])
        
    UserRepository.update(user_id, data)
    return {"status": "success", "message": "User updated successfully."}

@router.delete("/{user_id}", summary="Delete a user/bot")
def delete_worker(user_id: int, bot_manager = Depends(get_bot_manager)):
    """Deletes a user from the database and stops their bot if running."""
    user = UserRepository.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Stop the bot if it's currently running
    bot_manager.stop_user(user_id)
    UserRepository.delete(user_id)
    return {"status": "success", "message": "User deleted successfully."}

from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse
import pandas as pd
import io
from config.database import bulk_add_users, _simple_decode

@router.get("/export/excel", summary="Export all users to an Excel spreadsheet")
def export_excel():
    from config.database import _simple_decode
    users = UserRepository.get_all()
    if not users:
        raise HTTPException(status_code=404, detail="No users found to export")
    
    export_data = []
    for u in users:
        u_copy = dict(u)
        u_copy['password'] = _simple_decode(u_copy.get('password_enc', ''))
        u_copy['email_app_password'] = u_copy.get('email_app_password', '')
        export_data.append(u_copy)
        
    df = pd.DataFrame(export_data)
    cols = ["id", "is_active", "email", "password", "email_app_password", "first_name", "last_name", "phone", 
            "jurisdiction", "location", "category", "visa_type", "visa_sub_type", 
            "appointment_for", "minimum_days", "check_interval", "proxy_address", "status", "last_check", "error_msg", "cooldown_until"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Users')
    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="vize_hesaplari.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@router.post("/import/excel", summary="Bulk import users from an Excel spreadsheet")
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="Only .xlsx files are supported")
    
    try:
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content))
        df = df.where(pd.notnull(df), None)
        
        users_list = []
        for _, row in df.iterrows():
            if not row.get("email"):
                continue # Sadece Email zorunlu (Şifre boş bırakılabilir)
                
            user = {
                "is_active": True,
                "email": str(row.get("email")).strip(),
                "password": str(row.get("password", "")).strip() if "password" in row and pd.notnull(row.get("password")) else "",
                "first_name": str(row.get("first_name", "")).strip(),
                "last_name": str(row.get("last_name", "")).strip(),
                "phone": str(row.get("phone", "")).strip(),
                "jurisdiction": str(row.get("jurisdiction", "")).strip(),
                "location": str(row.get("location", "")).strip(),
                "category": str(row.get("category", "")).strip(),
                "visa_type": str(row.get("visa_type", "")).strip(),
                "visa_sub_type": str(row.get("visa_sub_type", "")).strip(),
                "appointment_for": str(row.get("appointment_for", "Individual")).strip(),
                "minimum_days": int(row.get("minimum_days", 0) if row.get("minimum_days") is not None else 0),
                "check_interval": int(row.get("check_interval", 60) if row.get("check_interval") is not None else 60),
                "proxy_address": str(row.get("proxy_address", "")).strip() if row.get("proxy_address") else "",
                "email_app_password": str(row.get("email_app_password", "")).strip() if "email_app_password" in row else "",
                "headless": True,
                "status": "Idle"
            }
            users_list.append(user)
            
        if users_list:
            bulk_add_users(users_list)
            return {"status": "success", "message": f"{len(users_list)} users imported successfully", "imported": len(users_list)}
        else:
            raise HTTPException(status_code=400, detail="No valid users found in spreadsheet. (An 'email' column with at least 1 email address is required)")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importing Excel: {str(e)}")
