from fastapi import APIRouter
from typing import List
from data.repositories import ProxyRepository
from bot.proxy_manager import proxy_manager

router = APIRouter()

@router.get("", summary="Return health metrics for all proxies")
def get_proxies():
    """Retrieve all proxies from the repository with their current fail/success counts."""
    return {"proxies": ProxyRepository.get_all()}

@router.post("/import", summary="Import a list of proxy strings")
def import_proxies(proxy_list: List[str]):
    """Receives a JSON list of proxy string addresses and bulk imports them."""
    proxy_manager.import_proxy_list(proxy_list)
    return {"status": "success", "imported": len(proxy_list)}

@router.delete("/{address:path}", summary="Delete a proxy")
def delete_proxy(address: str):
    ProxyRepository.delete(address)
    # Also remove from redis if active
    r = proxy_manager.redis
    if r:
        r.srem(proxy_manager.active_key, address)
        r.zrem(proxy_manager.cooldown_key, address)
    return {"status": "success", "message": f"Deleted {address}"}
