from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import os

from app.core.config import settings
from app.core.constants import DocumentTypeFilter, CrawlDepthOption, FILTER_DISPLAY_NAMES
from app.core.logging_config import setup_logging, get_logger
from app.api.routes import scraper, downloads, settings as settings_routes
from app.services.settings_service import runtime_settings

# Initialize logging
setup_logging(level=settings.LOG_LEVEL)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"Starting {settings.APP_NAME}")
    yield
    logger.info(f"Shutting down {settings.APP_NAME}")


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

logger.info("API routes registered")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page with scraping form."""
    logger.debug(f"Serving index page to {request.client.host if request.client else 'unknown'}")
    return templates.TemplateResponse(
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
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "title": f"Results - {settings.APP_NAME}",
            "session_id": session_id,
        },
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": settings.APP_NAME}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
