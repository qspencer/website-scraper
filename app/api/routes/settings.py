"""API routes for application settings."""

import re
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

# Matches a MongoDB URI containing a userinfo segment (user[:password]@host…).
# Group 1 = scheme prefix incl. "://", group 2 = host-and-rest.
_MONGO_URI_USERINFO_RE = re.compile(r"^(mongodb(?:\+srv)?://)[^@/]+@(.+)$")
_MONGO_URI_MASK = "****:****@"

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
    results_per_page: Optional[int] = None
    scan_history_limit: Optional[int] = None
    mongodb_uri: Optional[str] = None
    mongodb_database: Optional[str] = None
    ai_api_url: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    stirling_pdf_url: Optional[str] = None


class SettingsResponse(BaseModel):
    """Model for settings response."""
    max_pages_per_scan: int
    request_timeout: int
    requests_per_second: float
    default_crawl_depth: int
    max_crawl_depth: int
    max_concurrent_requests: int
    results_per_page: int
    scan_history_limit: int
    mongodb_uri: str
    mongodb_database: str
    ai_api_url: str
    ai_api_key: str
    ai_model: str
    stirling_pdf_url: str


def _mask_mongodb_uri(uri: str) -> str:
    """Mask the userinfo (user:password) in a MongoDB URI, if present.

    Returns the URI unchanged when there's no userinfo (e.g. mongodb://localhost:27017).
    """
    if not uri:
        return uri
    m = _MONGO_URI_USERINFO_RE.match(uri)
    if not m:
        return uri
    return f"{m.group(1)}{_MONGO_URI_MASK}{m.group(2)}"


def _masked_settings() -> dict:
    """Return settings with credentials masked for client responses."""
    all_settings = runtime_settings.get_all()
    if all_settings.get("ai_api_key"):
        all_settings["ai_api_key"] = "********"
    if all_settings.get("mongodb_uri"):
        all_settings["mongodb_uri"] = _mask_mongodb_uri(all_settings["mongodb_uri"])
    return all_settings


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Get current settings."""
    return _masked_settings()


@router.put("", response_model=SettingsResponse)
async def update_settings(updates: SettingsUpdate):
    """Update settings."""
    # Filter out None values
    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}

    # Don't overwrite non-empty string settings with empty strings.
    # This prevents password fields (which browsers may clear on page load)
    # from wiping saved values when the user saves other settings.
    STRING_KEYS = {"ai_api_url", "ai_api_key", "ai_model", "mongodb_uri", "mongodb_database"}
    for key in STRING_KEYS:
        if key in update_dict and update_dict[key] == "":
            current = getattr(runtime_settings, key, "")
            if current:
                del update_dict[key]

    # Ignore masked API key placeholder — means user didn't change it
    if update_dict.get("ai_api_key") == "********":
        del update_dict["ai_api_key"]

    # Ignore masked mongodb_uri (contains the userinfo mask we put there) — user didn't change it.
    if "mongodb_uri" in update_dict and _MONGO_URI_MASK in update_dict["mongodb_uri"]:
        del update_dict["mongodb_uri"]

    if update_dict:
        logger.info(f"Updating settings: {list(update_dict.keys())}")
        runtime_settings.update(update_dict)

    return _masked_settings()


@router.post("/reset", response_model=SettingsResponse)
async def reset_settings():
    """Reset settings to defaults."""
    runtime_settings.reset_to_defaults()
    return _masked_settings()
