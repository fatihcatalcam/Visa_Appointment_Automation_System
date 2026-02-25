import sqlite3
import os
import psycopg2
from config.pg_adapter import pg_manager
from config.database import DB_PATH, get_all_users, get_all_proxies
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """Migrates data from SQLite to PostgreSQL"""
    logger.info("Starting PostgreSQL Migration...")
    
    conn_pg = pg_manager.get_connection()
    if not conn_pg:
        logger.error("PostgreSQL connection failed! Cannot migrate.")
        return
        
    users = get_all_users()
    proxies = get_all_proxies()
    
    # Global Settings
    conn_sl = sqlite3.connect(DB_PATH)
    cur_sl = conn_sl.cursor()
    cur_sl.execute("SELECT * FROM global_settings")
    settings = cur_sl.fetchall()
    conn_sl.close()
    
    with conn_pg.cursor() as cur:
        # Migrate Global Settings
        for key, value in settings:
             cur.execute(
                 "INSERT INTO global_settings (key, value) VALUES (%s, %s) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", 
                 (key, str(value))
             )
        logger.info(f"Migrated {len(settings)} global settings.")
        
        # Migrate Proxies
        for p in proxies:
             cur.execute(
                 "INSERT INTO proxies (address, status, success_count, fail_count, consecutive_fails, last_used, disabled_until) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                 (p['address'], p.get('status', 'Active'), p.get('success_count', 0), p.get('fail_count', 0), 
                  p.get('consecutive_fails', 0), p.get('last_used'), p.get('disabled_until'))
             )
        logger.info(f"Migrated {len(proxies)} proxies.")
        
        # Migrate Users
        for u in users:
             # Convert numeric boolean representations if any are left from sqlite
             is_active = bool(u.get('is_active', True))
             headless = bool(u.get('headless', True))
             
             try:
                 cur.execute(
                     """INSERT INTO users (is_active, email, password_enc, first_name, last_name, phone, jurisdiction, 
                                           location, category, appointment_for, visa_type, visa_sub_type, proxy_address, 
                                           check_interval, minimum_days, headless, status, last_check, check_count, 
                                           error_msg, cooldown_until)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                     """,
                     (is_active, u['email'], u['password_enc'], u.get('first_name'), u.get('last_name'), u.get('phone'),
                      u.get('jurisdiction'), u.get('location'), u.get('category'), u.get('appointment_for', 'Individual'),
                      u.get('visa_type'), u.get('visa_sub_type'), u.get('proxy_address'), u.get('check_interval', 60),
                      u.get('minimum_days', 0), headless, u.get('status', 'Idle'), u.get('last_check'), 
                      u.get('check_count', 0), u.get('error_msg'), u.get('cooldown_until'))
                 )
             except psycopg2.errors.UniqueViolation:
                 conn_pg.rollback() # Skip user if email already migrated
                 logger.warning(f"Skipping duplicate user: {u['email']}")
                 continue
                 
        logger.info(f"Migrated {len(users)} user profiles.")
             
    logger.info("✅ PostgreSQL Migration Completed Successfully!")

if __name__ == "__main__":
    run_migration()
