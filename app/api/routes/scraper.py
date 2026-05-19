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
from app.services.history_service import save_scan
from app.services.settings_service import runtime_settings
from app.utils.file_utils import format_file_size
from app.schemas.document import DocumentInfo

logger = get_logger(__name__)

router = APIRouter(prefix="/api/scrape", tags=["scraper"])


def _save_scan_history(session: dict) -> None:
    """Save a completed scan to history."""
    try:
        request = session["request"]
        result = session.get("result")
        if not result:
            return

        start_time = session.get("start_time")
        end_time = session.get("end_time")
        duration = (end_time - start_time) if (start_time and end_time) else None

        # Compute size stats from documents
        docs_with_size = [d for d in result.documents if d.file_size_bytes and d.file_size_bytes > 0]
        total_size = sum(d.file_size_bytes for d in docs_with_size) if docs_with_size else None
        largest = max(docs_with_size, key=lambda d: d.file_size_bytes) if docs_with_size else None
        smallest = min(docs_with_size, key=lambda d: d.file_size_bytes) if docs_with_size else None

        scan_mode = "single page"
        if request.crawl_option.value == "follow":
            scan_mode = "continuous" if request.scan_all_pages else "batch"

        save_scan({
            "url": str(request.url),
            "crawl_option": request.crawl_option.value,
            "max_depth": request.max_depth,
            "scan_mode": scan_mode,
            "document_filter": request.document_type_filter.value,
            "pages_scanned": result.pages_scanned,
            "documents_found": len(result.documents),
            "scan_error_count": len(result.errors),
            "document_error_count": sum(1 for d in result.documents if not d.is_accessible),
            "duration_seconds": duration,
            "total_size_bytes": total_size,
            "largest_file_name": largest.filename if largest else None,
            "largest_file_size": largest.file_size_bytes if largest else None,
            "smallest_file_name": smallest.filename if smallest else None,
            "smallest_file_size": smallest.file_size_bytes if smallest else None,
        })
    except Exception as e:
        logger.error(f"Failed to save scan history: {e}", exc_info=True)

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
                scan_all_pages=request.scan_all_pages,
            ):
                # Check if cancelled
                if session.get("cancelled"):
                    logger.info(f"Session {session_id} cancelled by user")
                    # Save partial results from crawl state
                    partial_result = ScrapeResult(
                        success=True,
                        documents=crawl_state.all_documents,
                        pages_scanned=crawl_state.pages_scanned,
                        errors=crawl_state.errors,
                        has_more_pages=False,
                        pages_remaining=0,
                    )
                    session["result"] = partial_result
                    session["status"] = "complete"
                    session["end_time"] = time.time()
                    _save_scan_history(session)
                    yield {
                        "event": "cancelled",
                        "data": json.dumps({
                            "message": "Scraping cancelled",
                            "documents_found": len(crawl_state.all_documents),
                        }),
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
                    _save_scan_history(session)

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
        "scan_all_pages": request.scan_all_pages,
        "pages_scanned": pages_scanned,
        "documents_found": documents_found,
        "duration_seconds": duration_seconds,
        "has_more_pages": has_more_pages,
        "pages_remaining": pages_remaining,
        "scan_error_count": len(result.errors) if result else 0,
        "document_error_count": sum(1 for d in result.documents if not d.is_accessible) if result else 0,
        "has_retryable_errors": bool(
            (result and any(not d.is_accessible for d in result.documents)) or
            (session.get("crawl_state") and session["crawl_state"].failed_pages)
        ),
        "scan_errors": (result.errors[:50] if result else []),
        "failed_pages": [
            {"url": url, "depth": depth}
            for url, depth in (session.get("crawl_state").failed_pages if session.get("crawl_state") else [])
        ][:50],
        "document_errors": [
            {"filename": d.filename, "url": d.url, "error": d.error_message or "Not accessible"}
            for d in (result.documents if result else [])
            if not d.is_accessible
        ][:50],
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


@router.get("/retry/{session_id}")
async def retry_errors(session_id: str):
    """Retry failed pages and inaccessible documents via SSE."""
    if session_id not in scrape_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = scrape_sessions[session_id]
    result = session.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="No results to retry")

    crawl_state = session.get("crawl_state")
    request = session["request"]

    # Gather retryable items
    failed_docs = [d for d in result.documents if not d.is_accessible]
    failed_pages = list(crawl_state.failed_pages) if crawl_state else []

    if not failed_docs and not failed_pages:
        raise HTTPException(status_code=400, detail="No errors to retry")

    total_items = len(failed_docs) + len(failed_pages)
    logger.info(f"Retrying {len(failed_docs)} documents and {len(failed_pages)} pages "
                f"for session {session_id}")

    async def event_generator():
        import aiohttp
        completed = 0
        docs_fixed = 0
        pages_fixed = 0
        new_docs_found = 0

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=runtime_settings.request_timeout),
            headers={"User-Agent": "Mozilla/5.0 (compatible; DocumentScraper/1.0)"}
        ) as http_session:
            # Retry failed documents (re-do HEAD requests)
            for doc in failed_docs:
                completed += 1
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "completed": completed,
                        "total": total_items,
                        "message": f"Retrying document: {doc.filename}",
                    }),
                }

                try:
                    updated = await scraper_service.get_file_info(
                        http_session, doc.url, doc.source_page, doc.depth
                    )
                    if updated.is_accessible:
                        # Update the document in-place in the result
                        doc.is_accessible = True
                        doc.error_message = None
                        doc.file_size_bytes = updated.file_size_bytes
                        doc.file_size_display = updated.file_size_display
                        doc.filename = updated.filename
                        docs_fixed += 1
                except Exception as e:
                    logger.debug(f"Retry failed for document {doc.url}: {e}")

            # Retry failed pages (re-scan for documents)
            if failed_pages and crawl_state:
                for page_url, depth in failed_pages:
                    completed += 1
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "completed": completed,
                            "total": total_items,
                            "message": f"Retrying page: {page_url[:60]}...",
                        }),
                    }

                    try:
                        page_links, documents, warning = await scraper_service.scan_page(
                            http_session, page_url, request.document_type_filter, depth
                        )
                        pages_fixed += 1

                        # Add any new documents found
                        for doc in documents:
                            if doc.url not in crawl_state.document_urls:
                                filename_lower = doc.filename.lower()
                                if filename_lower not in crawl_state.document_filenames:
                                    crawl_state.document_urls.add(doc.url)
                                    crawl_state.document_filenames.add(filename_lower)
                                    crawl_state.all_documents.append(doc)
                                    result.documents.append(doc)
                                    new_docs_found += 1
                    except Exception as e:
                        logger.debug(f"Retry failed for page {page_url}: {e}")

                # Clear failed pages that succeeded
                if pages_fixed > 0:
                    # Remove pages that were successfully retried
                    crawl_state.failed_pages = [
                        (url, d) for url, d in crawl_state.failed_pages
                        if (url, d) not in failed_pages[:completed]
                    ]

            # Update error lists
            if crawl_state:
                # Rebuild errors: keep only errors for pages still failing
                still_failed_urls = {url for url, _ in crawl_state.failed_pages}
                crawl_state.errors = [
                    e for e in crawl_state.errors
                    if not any(url in e for url in
                              {url for url, _ in failed_pages} - still_failed_urls)
                ]
                result.errors = crawl_state.errors

            # Recount
            remaining_scan_errors = len(result.errors)
            remaining_doc_errors = sum(1 for d in result.documents if not d.is_accessible)

        logger.info(f"Retry complete for session {session_id}: "
                    f"{docs_fixed} docs fixed, {pages_fixed} pages fixed, "
                    f"{new_docs_found} new docs found")

        yield {
            "event": "complete",
            "data": json.dumps({
                "docs_fixed": docs_fixed,
                "pages_fixed": pages_fixed,
                "new_docs_found": new_docs_found,
                "remaining_scan_errors": remaining_scan_errors,
                "remaining_doc_errors": remaining_doc_errors,
                "total_documents": len(result.documents),
            }),
        }

    return EventSourceResponse(event_generator())


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
