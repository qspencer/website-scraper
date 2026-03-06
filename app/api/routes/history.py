"""API routes for scan history."""

from fastapi import APIRouter

from app.core.logging_config import get_logger
from app.services.history_service import get_history, clear_history

logger = get_logger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def list_history():
    """Get scan history."""
    return get_history()


@router.delete("")
async def delete_history():
    """Clear all scan history."""
    clear_history()
    return {"message": "History cleared"}
