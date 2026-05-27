from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import asyncio
import os
import time

from app.core.config import settings
from app.core.constants import DocumentTypeFilter, CrawlDepthOption, FILTER_DISPLAY_NAMES
from app.core.logging_config import setup_logging, get_logger
from app.api.routes import scraper, downloads, settings as settings_routes, history, categorize
from app.services import background_tasks, session_store
from app.services.settings_service import runtime_settings

# Initialize logging
setup_logging(level=settings.LOG_LEVEL)
logger = get_logger(__name__)

# Session-sweeper config. Sessions live in process memory; clients are expected to
# DELETE when done but often don't (tab close, browser crash). The sweeper bounds the
# leak by expiring sessions older than SESSION_TTL_SECONDS and capping total count.
SESSION_TTL_SECONDS = 24 * 60 * 60      # 24 hours
SESSION_MAX_COUNT = 500                 # hard cap; oldest evicted past this
SESSION_SWEEP_INTERVAL = 5 * 60         # 5 minutes


def _sweep_session_dict(name: str, sessions: dict, now: float) -> int:
    """Evict expired sessions and enforce the count cap. Returns # removed."""
    removed = 0
    # TTL eviction
    expired = [sid for sid, s in sessions.items()
               if now - s.get("start_time", now) > SESSION_TTL_SECONDS]
    for sid in expired:
        del sessions[sid]
        removed += 1
    # Hard cap: drop oldest first
    if len(sessions) > SESSION_MAX_COUNT:
        oldest = sorted(sessions.items(), key=lambda kv: kv[1].get("start_time", 0))
        overflow = len(sessions) - SESSION_MAX_COUNT
        for sid, _ in oldest[:overflow]:
            del sessions[sid]
            removed += 1
    if removed:
        logger.info("Session sweep: removed %d expired/overflow entries from %s", removed, name)
    return removed


async def _session_sweeper():
    """Periodic loop that prunes scrape_sessions and download_sessions."""
    while True:
        try:
            await asyncio.sleep(SESSION_SWEEP_INTERVAL)
            now = time.time()
            _sweep_session_dict("scrape_sessions", session_store.scrape_sessions, now)
            _sweep_session_dict("download_sessions", session_store.download_sessions, now)
            _sweep_session_dict("categorize_sessions", session_store.categorize_sessions, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a sweep failure kill the loop.
            logger.exception("Session sweep iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"Starting {settings.APP_NAME}")
    background_tasks.track(_session_sweeper(), name="session_sweeper")
    logger.info("Session sweeper started (interval=%ds, ttl=%ds, cap=%d)",
                SESSION_SWEEP_INTERVAL, SESSION_TTL_SECONDS, SESSION_MAX_COUNT)
    yield
    logger.info(f"Shutting down {settings.APP_NAME}")
    await background_tasks.cancel_all()


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="A web application to scrape websites for linked documents",
    version="1.0.0",
    lifespan=lifespan,
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions."""
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {str(exc)}",
        exc_info=True,
        extra={"path": request.url.path, "method": request.method}
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."}
    )


# Get the directory containing this file
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mount static files
static_dir = os.path.join(BASE_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Set up templates
templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)

# Include API routers
app.include_router(scraper.router)
app.include_router(downloads.router)
app.include_router(settings_routes.router)
app.include_router(history.router)
app.include_router(categorize.router)

logger.info("API routes registered")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page with scraping form."""
    logger.debug(f"Serving index page to {request.client.host if request.client else 'unknown'}")
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": settings.APP_NAME,
            "filter_options": [
                {"value": f.value, "label": FILTER_DISPLAY_NAMES[f]}
                for f in DocumentTypeFilter
            ],
            "default_filter": DocumentTypeFilter.COMMON.value,
            "default_depth": runtime_settings.default_crawl_depth,
            "max_depth": runtime_settings.max_crawl_depth,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page."""
    logger.debug("Serving settings page")
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "title": f"Settings - {settings.APP_NAME}",
            "settings": runtime_settings.get_all(),
        },
    )


@app.get("/results/{session_id}", response_class=HTMLResponse)
async def results_page(request: Request, session_id: str):
    """Results page showing found documents."""
    logger.debug(f"Serving results page for session {session_id}")
    # Look up the scan_url so the page can show per-scan summarization progress
    # instead of corpus-wide progress. Falls back to "" if the session has been
    # swept (24h TTL) — the page still works, just with global polling.
    scan_url = ""
    scrape_session = session_store.scrape_sessions.get(session_id)
    if scrape_session and scrape_session.get("request") is not None:
        scan_url = str(scrape_session["request"].url)
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "title": f"Results - {settings.APP_NAME}",
            "session_id": session_id,
            "scan_url": scan_url,
            "results_per_page": runtime_settings.results_per_page,
        },
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """Scan history page."""
    logger.debug("Serving history page")
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "title": f"History - {settings.APP_NAME}",
        },
    )


@app.get("/documents", response_class=HTMLResponse)
async def documents_page(request: Request):
    """Document search page."""
    logger.debug("Serving documents page")
    return templates.TemplateResponse(
        request,
        "documents.html",
        {
            "title": f"Documents - {settings.APP_NAME}",
        },
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": settings.APP_NAME}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
