"""HTTP API for the iterative document-categorization pipeline (M4 of the spec).

Lives under /api/download/mongodb/ to match the existing MongoDB-related routes.
The actual pipeline logic is in app.services.categorization_service; this layer
is just session bookkeeping + SSE wire-up + thin wrappers around the persisted
category-set helpers in mongodb_service.

Lifecycle of a categorize session:

    POST /categorize/start   → session_id (status="running", task scheduled)
        ↓
    GET  /categorize/progress/{id}  ← SSE stream of pipeline events
        ↓
    on "final" event, client decides to:
      POST /categorize/accept/{id}  → persisted, session dropped
      (or) POST /categorize/cancel/{id}  → task cancelled, session dropped

Sessions left untouched are evicted by the periodic sweeper in app.main after
SESSION_TTL_SECONDS.
"""

import asyncio
import functools
import json
import time
import uuid
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse

from app.core.logging_config import get_logger
from app.services import categorization_service, mongodb_service
from app.services.background_tasks import track
from app.services.session_store import categorize_sessions

logger = get_logger(__name__)

router = APIRouter(prefix="/api/download/mongodb", tags=["categorize"])


async def _run_sync(func, *args, **kwargs):
    """Run a sync function in a thread pool (mongodb_service is sync)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


# Sentinel pushed onto the event queue when the pipeline finishes, so the SSE
# generator knows to stop yielding rather than block forever.
_END_SENTINEL = object()


async def _run_pipeline_session(session_id: str, scan_url: str) -> None:
    """Run the pipeline for a session, forwarding events to its queue."""
    session = categorize_sessions[session_id]
    queue: asyncio.Queue = session["queue"]

    async def emit(event_type: str, payload: Dict[str, Any]) -> None:
        await queue.put({"type": event_type, "data": payload})

    try:
        result = await categorization_service.run_categorization_pipeline(scan_url, emit=emit)
        session["status"] = "complete"
        session["result"] = result
    except asyncio.CancelledError:
        session["status"] = "cancelled"
        await queue.put({"type": "cancelled", "data": {}})
        raise
    except categorization_service.CategorizationError as e:
        logger.warning(f"Categorize session {session_id} aborted: {e}")
        session["status"] = "error"
        session["error"] = str(e)
        await queue.put({"type": "error", "data": {"message": str(e)}})
    except Exception as e:
        logger.error(f"Categorize session {session_id} crashed", exc_info=True)
        session["status"] = "error"
        session["error"] = f"Unexpected: {e}"
        await queue.put({"type": "error", "data": {"message": f"Unexpected: {e}"}})
    finally:
        await queue.put(_END_SENTINEL)


# --- POST /categorize/start ------------------------------------------------


class CategorizeStartResponse(BaseModel):
    session_id: str
    status: str
    message: str


@router.post("/categorize/start", response_model=CategorizeStartResponse)
async def categorize_start(scan_url: str):
    """Kick off a categorization pipeline for ``scan_url``.

    Returns a session_id immediately; the actual work runs in the background.
    Connect to /categorize/progress/{id} to receive SSE events.
    """
    scan_url = scan_url.strip()
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")

    session_id = str(uuid.uuid4())
    categorize_sessions[session_id] = {
        "status": "running",
        "scan_url": scan_url,
        "start_time": time.time(),
        "queue": asyncio.Queue(),
        "result": None,
        "error": None,
        "task": None,
    }

    task = track(
        _run_pipeline_session(session_id, scan_url),
        name=f"categorize:{session_id[:8]}",
    )
    categorize_sessions[session_id]["task"] = task

    logger.info(f"Started categorize session {session_id} for {scan_url}")
    return CategorizeStartResponse(
        session_id=session_id, status="running",
        message="Categorization started in background",
    )


# --- GET /categorize/progress/{session_id} (SSE) ---------------------------


@router.get("/categorize/progress/{session_id}")
async def categorize_progress(session_id: str):
    """SSE stream of pipeline events.

    Emits: iteration_start, proposal, phase_progress, quality_report, final,
    error, cancelled. The stream closes after the terminal event.
    """
    if session_id not in categorize_sessions:
        raise HTTPException(status_code=404, detail="Categorize session not found")

    queue: asyncio.Queue = categorize_sessions[session_id]["queue"]

    async def event_generator():
        while True:
            event = await queue.get()
            if event is _END_SENTINEL:
                break
            yield {"event": event["type"], "data": json.dumps(event["data"])}

    return EventSourceResponse(event_generator())


# --- POST /categorize/accept/{session_id} ----------------------------------


class CategorizeAcceptResponse(BaseModel):
    set_id: str
    scan_url: str
    categories: list
    doc_count: int


@router.post("/categorize/accept/{session_id}", response_model=CategorizeAcceptResponse)
async def categorize_accept(session_id: str):
    """Persist a completed categorization to MongoDB and drop the session.

    Returns 400 if the session isn't in ``complete`` state.
    """
    if session_id not in categorize_sessions:
        raise HTTPException(status_code=404, detail="Categorize session not found")
    session = categorize_sessions[session_id]
    if session["status"] != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Session is in state {session['status']!r}; cannot accept.",
        )

    result = session["result"]
    set_id = await _run_sync(
        mongodb_service.accept_categorization,
        scan_url=result["scan_url"],
        categories=result["categories"],
        assignments=result["assignments"],
        iterations_used=result["iterations_used"],
        model=result["model"],
    )
    del categorize_sessions[session_id]

    return CategorizeAcceptResponse(
        set_id=set_id,
        scan_url=result["scan_url"],
        categories=result["categories"],
        doc_count=result["doc_count"],
    )


# --- POST /categorize/cancel/{session_id} ----------------------------------


@router.post("/categorize/cancel/{session_id}")
async def categorize_cancel(session_id: str):
    """Cancel a running categorization pipeline."""
    if session_id not in categorize_sessions:
        raise HTTPException(status_code=404, detail="Categorize session not found")
    session = categorize_sessions[session_id]
    task: Optional[asyncio.Task] = session.get("task")
    if task and not task.done():
        task.cancel()
        # The task's CancelledError handler sets status="cancelled" and pushes
        # the sentinel onto the queue, so SSE consumers close cleanly.
    else:
        session["status"] = "cancelled"
        await session["queue"].put(_END_SENTINEL)
    return {"status": "cancelled", "session_id": session_id}


# --- GET /categories?scan_url= ---------------------------------------------


@router.get("/categories")
async def get_categories(scan_url: str):
    """Return the persisted category set for a scan, with live document counts.

    Returns 404 if no categorization has been accepted for this scan yet.
    """
    scan_url = scan_url.strip()
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")
    cset = await _run_sync(mongodb_service.get_category_set, scan_url)
    if cset is None:
        raise HTTPException(status_code=404, detail="No categorization for this scan")
    counts = await _run_sync(mongodb_service.get_category_counts, scan_url)
    return {
        "scan_url": cset["scan_url"],
        "categories": cset["categories"],
        "iterations_used": cset.get("iterations_used"),
        "model": cset.get("model"),
        "created_at": cset.get("created_at"),
        "doc_count_at_creation": cset.get("doc_count_at_creation"),
        "live_counts": counts,
        "uncategorized_count": counts.get("", 0),
    }


# --- PATCH /categories?scan_url= -------------------------------------------


class CategoryEditRequest(BaseModel):
    """Edit operation on an accepted category set.

    - rename: ``name`` (existing) + ``new_name``
    - merge:  ``name`` (source) + ``target`` (existing destination)
    - delete: ``name``
    """
    action: Literal["rename", "merge", "delete"]
    name: str = Field(..., min_length=1)
    new_name: Optional[str] = None
    target: Optional[str] = None

    @model_validator(mode="after")
    def _check_action_fields(self):
        if self.action == "rename" and not (self.new_name and self.new_name.strip()):
            raise ValueError("rename requires non-empty new_name")
        if self.action == "merge" and not (self.target and self.target.strip()):
            raise ValueError("merge requires non-empty target")
        return self


@router.patch("/categories")
async def patch_categories(scan_url: str, req: CategoryEditRequest):
    """Apply a rename / merge / delete to the persisted category set."""
    scan_url = scan_url.strip()
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")
    try:
        if req.action == "rename":
            moved = await _run_sync(
                mongodb_service.rename_category, scan_url, req.name, req.new_name,
            )
        elif req.action == "merge":
            moved = await _run_sync(
                mongodb_service.merge_categories, scan_url, req.name, req.target,
            )
        else:  # delete
            moved = await _run_sync(
                mongodb_service.delete_category, scan_url, req.name,
            )
    except ValueError as e:
        # The mongodb_service edit helpers raise ValueError for unknown names,
        # collisions, or missing category sets — all 400s to the client.
        raise HTTPException(status_code=400, detail=str(e))
    return {"action": req.action, "documents_affected": moved}


# --- DELETE /categories?scan_url= ------------------------------------------


@router.delete("/categories")
async def delete_categories(scan_url: str):
    """Drop the whole category set for a scan and un-categorize all its docs."""
    scan_url = scan_url.strip()
    if not scan_url:
        raise HTTPException(status_code=400, detail="scan_url is required")
    removed = await _run_sync(mongodb_service.delete_category_set, scan_url)
    if not removed:
        raise HTTPException(status_code=404, detail="No categorization for this scan")
    return {"deleted": True, "scan_url": scan_url}
