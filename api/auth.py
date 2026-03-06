"""
API Key authentication dependency for FastAPI.

If 'api_key' is set in global_settings, all API requests must include
an X-API-Key header matching that value. If not set, auth is disabled
(backwards compatible for existing setups).
"""
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
import logging

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(_api_key_header)):
    """FastAPI dependency that checks the X-API-Key header against the DB value."""
    from data.repositories import GlobalSettingsRepository

    stored_key = GlobalSettingsRepository.get("api_key", "").strip()

    # If no API key is configured, auth is disabled (backward compatible)
    if not stored_key:
        return None

    if not api_key or api_key != stored_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Set the X-API-Key header.",
        )
    return api_key
