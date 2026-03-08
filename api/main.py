from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
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
        logger.info("BotManager initialized headlessly via FastAPI lifespan")
    else:
        logger.info("BotManager correctly inherited from Desktop GUI injection")
        
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down BotManager...")

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

def get_bot_manager(request: Request):
    """Dependency injection to get the singleton BotManager instance across all routes"""
    return request.app.state.bot_manager
