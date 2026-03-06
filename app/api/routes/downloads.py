import json
import uuid
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.core.logging_config import get_logger
from app.schemas.scrape import (
    DownloadRequest,
    DownloadStartResponse,
    PathValidationRequest,
    PathValidationResponse,
)
from app.schemas.document import DocumentInfo
from app.services.download_service import download_service
from app.utils.file_utils import validate_download_path, ensure_directory_exists
from app.api.routes.scraper import scrape_sessions

logger = get_logger(__name__)

router = APIRouter(prefix="/api/download", tags=["downloads"])

# In-memory download session storage
download_sessions: Dict[str, dict] = {}


@router.post("/validate-path", response_model=PathValidationResponse)
async def validate_path(request: PathValidationRequest):
    """Validate a download path."""
    logger.debug(f"Validating path: {request.path}")
    result = validate_download_path(request.path, required_bytes=request.required_bytes or 0)

    if result["valid"]:
        logger.debug(f"Path valid: {result['absolute_path']}")
    else:
        logger.debug(f"Path invalid: {result['message']}")

    return PathValidationResponse(**result)


@router.post("/create-directory")
async def create_directory(request: PathValidationRequest):
    """Create a directory for downloads."""
    if not request.path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    logger.info(f"Creating directory: {request.path}")
    success, result_msg = ensure_directory_exists(request.path)

    if success:
        logger.info(f"Directory created: {result_msg}")
        return {"success": True, "message": "Directory created", "absolute_path": result_msg}
    else:
        logger.warning(f"Failed to create directory: {result_msg}")
        raise HTTPException(status_code=400, detail=result_msg)


@router.post("/start", response_model=DownloadStartResponse)
async def start_download(request: DownloadRequest):
    """Start downloading selected documents."""
    logger.info(f"Download requested: {len(request.document_urls)} files to {request.download_path}")

    # Validate path first
    path_result = validate_download_path(request.download_path)
    if not path_result["valid"]:
        logger.warning(f"Invalid download path: {request.download_path} - {path_result['message']}")
        raise HTTPException(status_code=400, detail=path_result["message"])
    abs_path = path_result["absolute_path"]

    # Get documents from scrape session
    if request.session_id not in scrape_sessions:
        logger.warning(f"Download requested for unknown scrape session: {request.session_id}")
        raise HTTPException(status_code=404, detail="Scrape session not found")

    scrape_session = scrape_sessions[request.session_id]
    if scrape_session["result"] is None:
        logger.warning(f"Download requested but scrape session {request.session_id} has no results")
        raise HTTPException(status_code=400, detail="Scrape results not available")

    # Filter to only selected documents
    all_documents = scrape_session["result"].documents
    selected_docs = [
        doc for doc in all_documents
        if doc.url in request.document_urls
    ]

    if not selected_docs:
        logger.warning(f"No valid documents selected from session {request.session_id}")
        raise HTTPException(status_code=400, detail="No valid documents selected")

    # Create download session
    download_id = str(uuid.uuid4())
    download_sessions[download_id] = {
        "documents": selected_docs,
        "download_path": abs_path,
        "status": "pending",
        "cancelled": False,
    }

    logger.info(f"Download session created: {download_id} for {len(selected_docs)} files")

    return DownloadStartResponse(
        session_id=download_id,
        status="started",
        message=f"Download initiated for {len(selected_docs)} files",
    )


@router.get("/progress/{session_id}")
async def download_progress(session_id: str):
    """SSE endpoint for real-time download progress."""
    if session_id not in download_sessions:
        logger.warning(f"Progress requested for unknown download session: {session_id}")
        raise HTTPException(status_code=404, detail="Download session not found")

    session = download_sessions[session_id]
    documents = session["documents"]
    download_path = session["download_path"]

    logger.info(f"Starting download progress stream for session: {session_id}")

    async def event_generator():
        try:
            session["status"] = "downloading"

            async for update in download_service.download_batch(
                documents=documents,
                download_path=download_path,
            ):
                # Check if cancelled
                if session.get("cancelled"):
                    logger.info(f"Download session {session_id} cancelled by user")
                    yield {
                        "event": "cancelled",
                        "data": json.dumps({"message": "Download cancelled"}),
                    }
                    break

                yield {
                    "event": update.status,
                    "data": update.model_dump_json(),
                }

            session["status"] = "complete"
            logger.info(f"Download session {session_id} complete")

        except Exception as e:
            session["status"] = "error"
            logger.error(f"Download session {session_id} error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }

    return EventSourceResponse(event_generator())


@router.delete("/cancel/{session_id}")
async def cancel_download(session_id: str):
    """Cancel an ongoing download."""
    if session_id not in download_sessions:
        logger.warning(f"Cancel requested for unknown download session: {session_id}")
        raise HTTPException(status_code=404, detail="Download session not found")

    logger.info(f"Cancellation requested for download session: {session_id}")
    download_sessions[session_id]["cancelled"] = True
    return {"message": "Cancellation requested"}


@router.delete("/session/{session_id}")
async def cleanup_download_session(session_id: str):
    """Clean up a download session."""
    if session_id in download_sessions:
        del download_sessions[session_id]
        logger.debug(f"Cleaned up download session: {session_id}")
    return {"message": "Session cleaned up"}
