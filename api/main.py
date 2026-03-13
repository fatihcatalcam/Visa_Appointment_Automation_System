from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api.routers import workers, proxies, system
from api.auth import verify_api_key
import logging
import os

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager
from bot.manager import BotManager
import queue

@asynccontextmanager
async def lifespan(app: FastAPI):
    # If the desktop app injected a manager, use it. Otherwise, create a headless one.
    if not hasattr(app.state, "bot_manager") or app.state.bot_manager is None:
        log_queue = queue.Queue(maxsize=10000)
        manager = BotManager(log_queue=log_queue)
        app.state.bot_manager = manager
        
        # Headless (VPS) modunda Telegram kontrolcüsünü de başlat
        try:
            from bot.telegram_controller import TelegramBotDaemon, register_daemon
            telegram_daemon = TelegramBotDaemon(manager)
            telegram_daemon.start()
            register_daemon(telegram_daemon)
            app.state.telegram_daemon = telegram_daemon
        except Exception as e:
            logger.error(f"Telegram daemon başlatılamadı: {e}")
            
        logger.info("BotManager and Telegram Daemon initialized headlessly via FastAPI lifespan")
    else:
        logger.info("BotManager correctly inherited from Desktop GUI injection")
        
    try:
        yield
    finally:
        # Cleanup on shutdown
        logger.info("Shutting down services...")
        if hasattr(app.state, "telegram_daemon") and app.state.telegram_daemon:
            app.state.telegram_daemon.stop()
        if hasattr(app.state, "bot_manager") and app.state.bot_manager:
            app.state.bot_manager.stop_all()

# The global BotManager instance will be injected here during startup
# This allows the API to orchestrate the exact same memory space as the Tkinter UI
app = FastAPI(
    title="SaaS Bot Control API",
    description="Headless backend API for managing workers over VPS.",
    version="1.0.0",
    lifespan=lifespan,
    # NOTE: Auth intentionally NOT global — WebSocket endpoints cannot send X-API-Key headers.
    # Auth is applied per-router below instead.
)

# CORS configuration - Restricted to local dev server origins
default_cors = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
cors_str = os.getenv("CORS_ORIGINS", default_cors)
allow_origins_list = [origin.strip() for origin in cors_str.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include endpoint routers — auth on workers & proxies only, NOT on system (has WebSocket)
app.include_router(workers.router, prefix="/api/v1/workers", tags=["Workers"], dependencies=[Depends(verify_api_key)])
app.include_router(proxies.router, prefix="/api/v1/proxies", tags=["Proxies"], dependencies=[Depends(verify_api_key)])
app.include_router(system.router, prefix="/api/v1/system", tags=["System & Telemetry"])

@app.get("/health", tags=["System & Telemetry"])
def health_check():
    return {"status": "ok", "message": "FastAPI is running"}

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

@app.get("/metrics", tags=["System & Telemetry"])
def metrics_root():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# --- Serve Static Frontend Build ---
from fastapi.responses import FileResponse
from fastapi import HTTPException

frontend_dist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web_panel", "dist")

if os.path.isdir(frontend_dist_path):
    assets_path = os.path.join(frontend_dist_path, "assets")
    if os.path.isdir(assets_path):
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
    
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # Don't intercept API or metrics routes
        if full_path.startswith("api/") or full_path.startswith("metrics") or full_path.startswith("health"):
            raise HTTPException(status_code=404, detail="Not Found")
            
        index_path = os.path.join(frontend_dist_path, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "Web panel build bulunamadı"}
else:
    logger.warning(f"Frontend dist path not found at {frontend_dist_path}. Please run 'npm run build' inside web_panel/")

def get_bot_manager(request: Request):
    """Dependency injection to get the singleton BotManager instance across all routes"""
    return request.app.state.bot_manager
