import asyncio
import json
import time
import uuid
from typing import Dict

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.core.logging_config import get_logger
from app.schemas.scrape import (
    ScrapeRequest,
    ScrapeStartResponse,
    ScrapeProgress,
    ScrapeResult,
)
from app.services.crawler_service import crawler_service, CrawlState
from app.services.scraper_service import scraper_service
from app.utils.file_utils import format_file_size

logger = get_logger(__name__)

router = APIRouter(prefix="/api/scrape", tags=["scraper"])

# In-memory session storage (in production, use Redis or similar)
scrape_sessions: Dict[str, dict] = {}


@router.post("/start", response_model=ScrapeStartResponse)
async def start_scrape(request: ScrapeRequest):
    """Start a new scraping session."""
    session_id = str(uuid.uuid4())

    logger.info(f"New scrape session: {session_id} for URL: {request.url}")
    logger.debug(f"Session {session_id} params: filter={request.document_type_filter.value}, "
                f"crawl={request.crawl_option.value}, depth={request.max_depth}")

    # Store session info
    scrape_sessions[session_id] = {
        "request": request,
        "status": "pending",
        "result": None,
        "cancelled": False,
        "crawl_state": None,  # Will hold CrawlState for continuation
        "start_time": time.time(),  # Track when scan started
        "end_time": None,
    }

    return ScrapeStartResponse(
        session_id=session_id,
        status="started",
        message="Scraping session initiated",
    )


@router.post("/continue/{session_id}", response_model=ScrapeStartResponse)
async def continue_scrape(session_id: str):
    """Continue a scraping session that has more pages."""
    if session_id not in scrape_sessions:
        logger.warning(f"Continue requested for unknown session: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    session = scrape_sessions[session_id]

    if session["crawl_state"] is None:
        raise HTTPException(status_code=400, detail="No continuation state available")

    if len(session["crawl_state"].pending_queue) == 0:
        raise HTTPException(status_code=400, detail="No more pages to scan")

    logger.info(f"Continuing scrape session: {session_id}, "
               f"{len(session['crawl_state'].pending_queue)} pages remaining")

    session["status"] = "pending"
    session["cancelled"] = False

    return ScrapeStartResponse(
        session_id=session_id,
        status="continuing",
        message=f"Continuing scan with {len(session['crawl_state'].pending_queue)} pages remaining",
    )


@router.get("/progress/{session_id}")
async def scrape_progress(session_id: str):
    """SSE endpoint for real-time scrape progress."""
    if session_id not in scrape_sessions:
        logger.warning(f"Progress requested for unknown session: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    session = scrape_sessions[session_id]
    request = session["request"]

    # Check if this is a continuation
    existing_state = session.get("crawl_state")
    is_continuation = existing_state is not None and len(existing_state.pending_queue) > 0

    logger.info(f"Starting scrape progress stream for session: {session_id}"
               f"{' (continuation)' if is_continuation else ''}")

    async def event_generator():
        try:
            session["status"] = "scanning"

            # Use existing state for continuation, or create new state for new crawl
            # Always pass a state object so it gets populated with crawl data
            if is_continuation:
                crawl_state = existing_state
            else:
                crawl_state = CrawlState()
                session["crawl_state"] = crawl_state  # Store reference immediately

            async for update in crawler_service.crawl(
                start_url=str(request.url),
                filter_type=request.document_type_filter,
                crawl_option=request.crawl_option,
                max_depth=request.max_depth,
                state=crawl_state,
            ):
                # Check if cancelled
                if session.get("cancelled"):
                    logger.info(f"Session {session_id} cancelled by user")
                    yield {
                        "event": "cancelled",
                        "data": json.dumps({"message": "Scraping cancelled"}),
                    }
                    break

                if isinstance(update, ScrapeProgress):
                    yield {
                        "event": "progress",
                        "data": update.model_dump_json(),
                    }
                elif isinstance(update, ScrapeResult):
                    # State is already stored and populated by reference
                    session["result"] = update
                    session["status"] = "complete"
                    session["end_time"] = time.time()

                    logger.info(f"Session {session_id} complete: "
                               f"{len(update.documents)} documents found, "
                               f"{update.pages_remaining} pages remaining")
                    yield {
                        "event": "complete",
                        "data": update.model_dump_json(),
                    }

        except Exception as e:
            session["status"] = "error"
            logger.error(f"Session {session_id} error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }

    return EventSourceResponse(event_generator())


@router.get("/results/{session_id}", response_model=ScrapeResult)
async def get_results(session_id: str):
    """Get the results of a completed scrape session."""
    if session_id not in scrape_sessions:
        logger.warning(f"Results requested for unknown session: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    session = scrape_sessions[session_id]

    if session["status"] == "pending":
        logger.debug(f"Results requested but session {session_id} not yet started")
        raise HTTPException(status_code=400, detail="Scraping not yet started")

    if session["result"] is None:
        logger.debug(f"Results requested but session {session_id} has no results")
        raise HTTPException(status_code=400, detail="Results not yet available")

    logger.debug(f"Returning results for session {session_id}")
    return session["result"]


@router.get("/summary/{session_id}")
async def get_scan_summary(session_id: str):
    """Get scan summary including timing and metadata."""
    if session_id not in scrape_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = scrape_sessions[session_id]
    request = session["request"]

    start_time = session.get("start_time")
    end_time = session.get("end_time")

    if start_time and end_time:
        duration_seconds = end_time - start_time
    else:
        duration_seconds = None

    result = session.get("result")
    pages_scanned = result.pages_scanned if result else 0
    documents_found = len(result.documents) if result else 0
    has_more_pages = result.has_more_pages if result else False
    pages_remaining = result.pages_remaining if result else 0

    return {
        "url": str(request.url),
        "crawl_option": request.crawl_option.value,
        "max_depth": request.max_depth,
        "document_filter": request.document_type_filter.value,
        "pages_scanned": pages_scanned,
        "documents_found": documents_found,
        "duration_seconds": duration_seconds,
        "has_more_pages": has_more_pages,
        "pages_remaining": pages_remaining,
    }


@router.delete("/cancel/{session_id}")
async def cancel_scrape(session_id: str):
    """Cancel an ongoing scrape session."""
    if session_id not in scrape_sessions:
        logger.warning(f"Cancel requested for unknown session: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    logger.info(f"Cancellation requested for session: {session_id}")
    scrape_sessions[session_id]["cancelled"] = True
    return {"message": "Cancellation requested"}


@router.delete("/session/{session_id}")
async def cleanup_session(session_id: str):
    """Clean up a scrape session."""
    if session_id in scrape_sessions:
        del scrape_sessions[session_id]
        logger.debug(f"Cleaned up session: {session_id}")
    return {"message": "Session cleaned up"}


@router.post("/calculate-sizes/{session_id}")
async def calculate_file_sizes(session_id: str, request: dict):
    """
    Calculate accurate file sizes for selected documents with SSE progress.
    Uses GET with Range header for more reliable size detection.
    """
    if session_id not in scrape_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    urls = request.get("urls", [])
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    logger.info(f"Calculating sizes for {len(urls)} files in session {session_id}")

    async def event_generator():
        import aiohttp

        results = []
        total_bytes = 0
        known_count = 0
        completed = 0

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (compatible; DocumentScraper/1.0)"}
        ) as session:
            for url in urls:
                completed += 1
                size = await scraper_service.get_file_size_via_get(session, url)

                if size is not None:
                    total_bytes += size
                    known_count += 1
                    results.append({
                        "url": url,
                        "size_bytes": size,
                        "size_display": format_file_size(size)
                    })
                else:
                    results.append({
                        "url": url,
                        "size_bytes": None,
                        "size_display": None
                    })

                # Send progress update
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "completed": completed,
                        "total": len(urls),
                        "current_url": url[:50] + "..." if len(url) > 50 else url,
                        "known_count": known_count,
                        "total_bytes": total_bytes,
                        "total_display": format_file_size(total_bytes)
                    })
                }

        # Send complete event
        logger.info(f"Size calculation complete: {known_count} known, "
                   f"{len(urls) - known_count} unknown, total {format_file_size(total_bytes)}")

        yield {
            "event": "complete",
            "data": json.dumps({
                "files": results,
                "total_bytes": total_bytes,
                "total_display": format_file_size(total_bytes),
                "known_count": known_count,
                "unknown_count": len(urls) - known_count
            })
        }

    return EventSourceResponse(event_generator())
