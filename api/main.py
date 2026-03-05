from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from api.routers import workers, proxies, system
import logging

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager
from bot.manager import BotManager
import queue

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the core task runner with bounded queue (P1: prevent memory leak)
    log_queue = queue.Queue(maxsize=10000)
    manager = BotManager(log_queue=log_queue)
    app.state.bot_manager = manager
    logger.info("BotManager initialized and bound to app.state")
    
    # In the future, this is where we'd start the proxy tester thread, etc.
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down BotManager...")

# The global BotManager instance will be injected here during startup
# This allows the API to orchestrate the exact same memory space as the Tkinter UI
app = FastAPI(
    title="SaaS Bot Control API",
    description="Headless backend API for managing workers over VPS.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration - Allow modern web dash to hit the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For production this should be restricted to the real domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include endpoint routers
app.include_router(workers.router, prefix="/api/v1/workers", tags=["Workers"])
app.include_router(proxies.router, prefix="/api/v1/proxies", tags=["Proxies"])
app.include_router(system.router, prefix="/api/v1/system", tags=["System & Telemetry"])

@app.get("/health", tags=["System & Telemetry"])
def health_check():
    return {"status": "ok", "message": "FastAPI is running"}

def get_bot_manager(request: Request):
    """Dependency injection to get the singleton BotManager instance across all routes"""
    return request.app.state.bot_manager
