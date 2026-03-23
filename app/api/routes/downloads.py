import asyncio
import csv
import io
import json
import uuid
from typing import Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
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
from app.services import mongodb_service
from app.services import ai_summarization_service
from app.services.text_extraction_service import extract_text
from app.utils.file_utils import validate_download_path, ensure_directory_exists
from app.services.settings_service import runtime_settings
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


@router.get("/mongodb/status")
async def mongodb_status():
    """Test MongoDB connection and return status."""
    result = mongodb_service.test_connection()
    return result


@router.post("/mongodb/start")
async def start_mongodb_download(request: DownloadRequest):
    """Download selected documents into MongoDB."""
    logger.info(f"MongoDB download requested: {len(request.document_urls)} files")

    # Test MongoDB connection first
    status = mongodb_service.test_connection()
    if not status["connected"]:
        raise HTTPException(status_code=400, detail=f"MongoDB not available: {status['message']}")

    # Get documents from scrape session
    if request.session_id not in scrape_sessions:
        raise HTTPException(status_code=404, detail="Scrape session not found")

    scrape_session = scrape_sessions[request.session_id]
    if scrape_session["result"] is None:
        raise HTTPException(status_code=400, detail="Scrape results not available")

    all_documents = scrape_session["result"].documents
    selected_docs = [doc for doc in all_documents if doc.url in request.document_urls]

    if not selected_docs:
        raise HTTPException(status_code=400, detail="No valid documents selected")

    # Get the scan URL from the scrape session request
    scan_url = str(scrape_session["request"].url)

    # Create download session
    download_id = str(uuid.uuid4())
    download_sessions[download_id] = {
        "documents": selected_docs,
        "download_path": "mongodb",
        "scan_url": scan_url,
        "status": "pending",
        "cancelled": False,
    }

    logger.info(f"MongoDB download session created: {download_id} for {len(selected_docs)} files")

    return DownloadStartResponse(
        session_id=download_id,
        status="started",
        message=f"MongoDB download initiated for {len(selected_docs)} files",
    )


@router.get("/mongodb/progress/{session_id}")
async def mongodb_download_progress(session_id: str):
    """SSE endpoint for MongoDB download progress."""
    if session_id not in download_sessions:
        raise HTTPException(status_code=404, detail="Download session not found")

    session = download_sessions[session_id]
    documents = session["documents"]
    scan_url = session.get("scan_url", "")

    logger.info(f"Starting MongoDB download for session: {session_id}")

    async def event_generator():
        import aiohttp

        total = len(documents)
        completed = 0
        failed = 0
        stored_ids = []

        try:
            session_obj = download_sessions[session_id]
            session_obj["status"] = "downloading"

            timeout = aiohttp.ClientTimeout(total=300)
            headers = {"User-Agent": "Mozilla/5.0 (compatible; DocumentScraper/1.0)"}

            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http_session:
                for i, doc in enumerate(documents):
                    if session_obj.get("cancelled"):
                        yield {
                            "event": "cancelled",
                            "data": json.dumps({"message": "Download cancelled"}),
                        }
                        break

                    yield {
                        "event": "downloading",
                        "data": json.dumps({
                            "status": "downloading",
                            "current_file": doc.filename,
                            "current_file_index": i + 1,
                            "total_files": total,
                            "files_completed": completed,
                            "files_failed": failed,
                            "message": f"Downloading {doc.filename}...",
                        }),
                    }

                    try:
                        async with http_session.get(doc.url) as response:
                            if response.status != 200:
                                failed += 1
                                logger.warning(f"MongoDB download failed: {doc.url} (HTTP {response.status})")
                                continue

                            file_data = await response.read()

                        # Extract text
                        extracted_text, extraction_status = extract_text(file_data, doc.extension)

                        # Determine content type
                        content_type_map = {
                            ".pdf": "application/pdf",
                            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            ".doc": "application/msword",
                            ".xls": "application/vnd.ms-excel",
                            ".txt": "text/plain",
                            ".csv": "text/csv",
                        }
                        content_type = content_type_map.get(doc.extension.lower(), "application/octet-stream")

                        # Store in MongoDB
                        doc_id = mongodb_service.store_document(
                            file_data=file_data,
                            filename=doc.filename,
                            extension=doc.extension,
                            source_url=doc.url,
                            source_page=doc.source_page,
                            scan_url=scan_url,
                            file_size_bytes=len(file_data),
                            content_type=content_type,
                            extracted_text=extracted_text,
                            text_extraction_status=extraction_status,
                        )
                        stored_ids.append(doc_id)
                        completed += 1

                    except Exception as e:
                        failed += 1
                        logger.error(f"Failed to download/store {doc.filename}: {e}")

            session_obj["status"] = "complete"

        except Exception as e:
            logger.error(f"MongoDB download error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }
            return

        logger.info(f"MongoDB download complete: {completed} stored, {failed} failed")

        # Check if AI summarization is configured
        ai_configured = bool(
            runtime_settings.ai_api_url
            and runtime_settings.ai_api_key
            and runtime_settings.ai_model
        )

        yield {
            "event": "complete",
            "data": json.dumps({
                "status": "complete",
                "total_files": total,
                "files_completed": completed,
                "files_failed": failed,
                "message": f"Stored {completed} files in MongoDB, {failed} failed",
                "stored_ids": stored_ids,
                "summarization_started": ai_configured and completed > 0,
            }),
        }

        # Kick off background summarization if AI is configured
        if ai_configured and completed > 0:
            logger.info("Starting background summarization")
            asyncio.create_task(ai_summarization_service.summarize_pending_documents())

    return EventSourceResponse(event_generator())


@router.delete("/session/{session_id}")
async def cleanup_download_session(session_id: str):
    """Clean up a download session."""
    if session_id in download_sessions:
        del download_sessions[session_id]
        logger.debug(f"Cleaned up download session: {session_id}")
    return {"message": "Session cleaned up"}


# ---------------------------------------------------------------------------
# AI Summarization endpoints
# ---------------------------------------------------------------------------


@router.get("/mongodb/summarization/status")
async def summarization_status():
    """Get summarization status and stats."""
    try:
        stats = mongodb_service.get_summary_stats()
    except Exception:
        stats = {"pending": 0, "complete": 0, "failed": 0, "skipped": 0}

    ai_configured = bool(
        runtime_settings.ai_api_url
        and runtime_settings.ai_api_key
        and runtime_settings.ai_model
    )

    return {
        "ai_configured": ai_configured,
        "is_running": ai_summarization_service.is_running(),
        "stats": stats,
    }


@router.post("/mongodb/summarization/start")
async def start_summarization():
    """Manually trigger summarization of pending documents."""
    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        raise HTTPException(
            status_code=400,
            detail="AI API is not configured. Set the API URL, key, and model in Settings.",
        )

    if ai_summarization_service.is_running():
        return {"message": "Summarization is already running", "started": False}

    asyncio.create_task(ai_summarization_service.summarize_pending_documents())
    return {"message": "Summarization started in background", "started": True}


@router.get("/mongodb/search")
async def search_mongodb_documents(
    q: str = "",
    scan_url: str = "",
    extension: str = "",
    limit: int = 50,
):
    """Search stored documents by text query and/or filters."""
    try:
        results = mongodb_service.search_documents(
            query=q,
            scan_url=scan_url or None,
            extension=extension or None,
            limit=min(limit, 200),
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@router.get("/mongodb/scans")
async def get_stored_scans():
    """Get distinct scan URLs with document counts."""
    try:
        col = mongodb_service._get_collection()
        pipeline = [
            {"$group": {
                "_id": "$scan_url",
                "count": {"$sum": 1},
                "latest": {"$max": "$downloaded_at"},
            }},
            {"$sort": {"latest": -1}},
        ]
        scans = []
        for row in col.aggregate(pipeline):
            scans.append({
                "scan_url": row["_id"],
                "document_count": row["count"],
                "latest_download": row["latest"].isoformat() if row.get("latest") else None,
            })
        return {"scans": scans}
    except Exception as e:
        logger.error(f"Failed to get scans: {e}")
        return {"scans": []}


@router.get("/mongodb/scan/summary-stats")
async def scan_summary_stats(scan_url: str):
    """Get summary stats for a specific scan."""
    try:
        stats = mongodb_service.get_scan_summary_stats(scan_url)
        return stats
    except Exception as e:
        logger.error(f"Failed to get scan summary stats: {e}")
        return {"pending": 0, "complete": 0, "failed": 0, "skipped": 0}


@router.post("/mongodb/scan/summarize")
async def summarize_scan(request: dict):
    """Start or restart summarization for a specific scan."""
    scan_url = request.get("scan_url", "")
    recreate = request.get("recreate", False)

    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")

    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        raise HTTPException(
            status_code=400,
            detail="AI API is not configured. Set the API URL, key, and model in Settings.",
        )

    if recreate:
        reset_count = mongodb_service.reset_scan_summaries(scan_url)
        logger.info(f"Reset {reset_count} summaries for scan: {scan_url}")

    if ai_summarization_service.is_running():
        return {"message": "Summarization is already running", "started": False}

    asyncio.create_task(ai_summarization_service.summarize_pending_documents())
    return {"message": "Summarization started in background", "started": True}


@router.post("/mongodb/scan/retry-failed")
async def retry_scan_failed(request: dict):
    """Retry failed summaries for a specific scan."""
    scan_url = request.get("scan_url", "")
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")

    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        raise HTTPException(
            status_code=400,
            detail="AI API is not configured. Set the API URL, key, and model in Settings.",
        )

    reset_count = mongodb_service.reset_scan_failed_summaries(scan_url)
    logger.info(f"Reset {reset_count} failed summaries for scan: {scan_url}")

    if reset_count == 0:
        return {"message": "No failed summaries to retry", "reset_count": 0, "started": False}

    if ai_summarization_service.is_running():
        return {
            "message": f"Reset {reset_count} failed summaries. Summarization is already running and will pick them up.",
            "reset_count": reset_count,
            "started": False,
        }

    asyncio.create_task(ai_summarization_service.summarize_pending_documents())
    return {
        "message": f"Reset {reset_count} failed summaries, retrying in background",
        "reset_count": reset_count,
        "started": True,
    }


@router.get("/mongodb/scan/failed-details")
async def get_scan_failed_details(scan_url: str = ""):
    """Get details of failed summarizations for a specific scan."""
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")

    try:
        failures = mongodb_service.get_scan_failed_summary_errors(scan_url)
        return {"failures": failures, "count": len(failures)}
    except Exception as e:
        logger.error(f"Failed to get failure details: {e}")
        return {"failures": [], "count": 0}


@router.get("/mongodb/extensions")
async def get_document_extensions(scan_url: str = ""):
    """Get distinct file extensions, optionally filtered by scan URL."""
    try:
        col = mongodb_service._get_collection()
        filter_dict = {}
        if scan_url:
            filter_dict["scan_url"] = scan_url
        extensions = sorted(col.distinct("extension", filter_dict))
        return {"extensions": extensions}
    except Exception as e:
        logger.error(f"Failed to get extensions: {e}")
        return {"extensions": []}


@router.get("/mongodb/document/{doc_id}")
async def get_document_detail(doc_id: str):
    """Get full document metadata by ID."""
    doc = mongodb_service.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/mongodb/document/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document from MongoDB."""
    deleted = mongodb_service.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted"}


@router.get("/mongodb/export/csv")
async def export_scan_csv(scan_url: str = ""):
    """Export documents for a scan as a CSV file."""
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url query parameter is required")

    try:
        documents = mongodb_service.get_documents_for_export(scan_url)
    except Exception as e:
        logger.error(f"CSV export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Document Title",
        "Short Summary",
        "Document URL",
        "Filename",
        "Document Type",
        "Version",
    ])

    # Sort alphabetically by title, falling back to filename for untitled docs
    documents.sort(key=lambda d: (d.get("title") or d.get("filename") or "").lower())

    for doc in documents:
        writer.writerow([
            doc.get("title") or "<automated title extraction failed>",
            doc.get("short_summary") or "<automated summary extraction failed>",
            doc.get("source_url") or "",
            doc.get("filename") or "",
            doc.get("file_type_label") or "",
            doc.get("version_label") or "",
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="document_export.csv"'},
    )


@router.post("/mongodb/summarization/retry")
async def retry_failed_summaries():
    """Reset failed summaries and re-run summarization."""
    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        raise HTTPException(
            status_code=400,
            detail="AI API is not configured. Set the API URL, key, and model in Settings.",
        )

    try:
        reset_count = mongodb_service.reset_failed_summaries()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset summaries: {e}")

    if reset_count == 0:
        return {"message": "No failed summaries to retry", "reset_count": 0, "started": False}

    asyncio.create_task(ai_summarization_service.summarize_pending_documents())
    return {
        "message": f"Reset {reset_count} failed summaries, re-running in background",
        "reset_count": reset_count,
        "started": True,
    }
