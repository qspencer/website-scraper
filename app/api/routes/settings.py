"""API routes for application settings."""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Model for updating settings. Values are clamped to valid ranges by the service."""
    max_pages_per_scan: Optional[int] = None
    request_timeout: Optional[int] = None
    requests_per_second: Optional[float] = None
    default_crawl_depth: Optional[int] = None
    max_crawl_depth: Optional[int] = None
    max_concurrent_requests: Optional[int] = None
    scan_history_limit: Optional[int] = None
    mongodb_uri: Optional[str] = None
    mongodb_database: Optional[str] = None
    ai_api_url: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None


class SettingsResponse(BaseModel):
    """Model for settings response."""
    max_pages_per_scan: int
    request_timeout: int
    requests_per_second: float
    default_crawl_depth: int
    max_crawl_depth: int
    max_concurrent_requests: int
    scan_history_limit: int
    mongodb_uri: str
    mongodb_database: str
    ai_api_url: str
    ai_api_key: str
    ai_model: str


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Get current settings."""
    return runtime_settings.get_all()


@router.put("", response_model=SettingsResponse)
async def update_settings(updates: SettingsUpdate):
    """Update settings."""
    # Filter out None values
    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}

    if update_dict:
        logger.info(f"Updating settings: {update_dict}")
        runtime_settings.update(update_dict)

    return runtime_settings.get_all()


@router.post("/reset", response_model=SettingsResponse)
async def reset_settings():
    """Reset settings to defaults."""
    runtime_settings.reset_to_defaults()
    return runtime_settings.get_all()
