# Document Scraper - Architecture Documentation

> **Status note (2026-05-19).** This document was first written before MongoDB storage,
> AI summarization, Stirling-PDF OCR, the Documents page, and persistent scan history
> were added. Several sections below (project tree, component list, architecture diagram,
> endpoint table, scan_history note) have been brought current. The data-flow narratives
> for scraping and downloading still hold. The README and the live `/docs` (Swagger UI)
> remain the most up-to-date sources for the user-facing feature list and the API surface.

## Overview

Document Scraper is a Python web application that scans websites for linked documents,
displays a preview of found files, and lets users either download them to a local
directory or store them in MongoDB (with full-text search and optional AI summaries).
It is a single-user local utility — no authentication, no multi-tenancy.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend Framework | FastAPI |
| ASGI Server | Uvicorn |
| HTTP Client | aiohttp |
| HTML Parser | BeautifulSoup4 + lxml |
| Browser Automation | Playwright (for JavaScript-rendered pages) |
| Real-time Updates | Server-Sent Events (sse-starlette) |
| Templating | Jinja2 |
| CSS Framework | Tailwind CSS (CDN) |
| Validation | Pydantic |
| Testing | pytest + pytest-asyncio |

## Project Structure

```
website-scraper/
├── app/
│   ├── main.py                          # FastAPI app + lifespan (session sweeper)
│   ├── core/
│   │   ├── config.py                    # Pydantic settings (env-overridable)
│   │   ├── constants.py                 # File extensions, enums, display names
│   │   ├── database.py                  # SQLite persistence (settings + scan_history)
│   │   └── logging_config.py            # Rotating file + console logging
│   ├── api/routes/
│   │   ├── scraper.py                   # Scan / continue / retry / cancel endpoints
│   │   ├── downloads.py                 # File-system + MongoDB + summarization endpoints
│   │   ├── settings.py                  # Settings GET/PUT/reset (masks secrets)
│   │   └── history.py                   # Scan history endpoints
│   ├── schemas/
│   │   ├── document.py                  # DocumentInfo, DownloadProgress models
│   │   └── scrape.py                    # Request/response models
│   ├── services/
│   │   ├── scraper_service.py           # Core scraping logic
│   │   ├── crawler_service.py           # Multi-page BFS crawling
│   │   ├── browser_service.py           # Playwright fallback for JS pages
│   │   ├── download_service.py          # Local-disk download streaming
│   │   ├── mongodb_service.py           # MongoDB+GridFS document storage
│   │   ├── text_extraction_service.py   # PDF / DOCX / XLSX / TXT extraction
│   │   ├── stirling_pdf_service.py      # Stirling-PDF OCR fallback
│   │   ├── ai_summarization_service.py  # LLM summarization (Anthropic/OpenAI/compat)
│   │   ├── settings_service.py          # Runtime settings (SQLite-backed singleton)
│   │   ├── history_service.py           # scan_history persistence
│   │   └── background_tasks.py          # Tracks fire-and-forget asyncio tasks
│   ├── utils/
│   │   ├── url_utils.py                 # URL validation, normalization
│   │   ├── file_utils.py                # File size / path validation / sanitization
│   │   └── document_types.py            # MIME / extension classification
│   └── templates/
│       ├── base.html                    # Base template (Tailwind via CDN)
│       ├── index.html                   # Main scan form
│       ├── results.html                 # Document preview + download / MongoDB store
│       ├── documents.html               # Search across MongoDB-stored documents
│       ├── history.html                 # Past-scan list
│       └── settings.html                # Settings page
├── scripts/
│   └── run.sh                           # Canonical entry point (venv + Stirling + uvicorn)
├── tests/
│   ├── test_*.py                        # 449 unit tests (mocks for Mongo / AI / browser)
│   └── integration/                     # Opt-in integration tier (real backends)
├── static/{css,js,images}/              # Favicon, small shared JS
├── docs/
│   ├── architecture.md                  # This document
│   └── user-guide.md                    # Non-technical walkthrough
├── logs/                                # Runtime logs (rotating, capped at ~30 MB)
├── requirements.txt
└── README.md
```

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                Web Browser                                │
│  index.html  →  results.html  →  documents.html / history.html / settings │
└────────────────┬──────────────────────────────────────────┬───────────────┘
                 │ HTTP + SSE                               │ HTTP
                 ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Application                              │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Routes:  scraper.py  downloads.py  settings.py  history.py        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Services:                                                          │  │
│  │    scraper_service  crawler_service  browser_service                │  │
│  │    download_service mongodb_service  text_extraction_service        │  │
│  │    stirling_pdf_service  ai_summarization_service                   │  │
│  │    settings_service  history_service  background_tasks              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────┬─────────────┬───────────────────┬──────────────────┬────────────┘
         │             │                   │                  │
         ▼             ▼                   ▼                  ▼
   external web   local disk         MongoDB / GridFS    Stirling-PDF
   (target URLs)  (./downloads)      (localhost:27017)   docker container
                                            │                  
                                            ▼
                                  AI provider (Anthropic /
                                  OpenAI / compatible endpoint)
```

Two persistence layers run alongside the in-memory session dicts:

- **SQLite (`scraper.db`)** — user-configurable settings and scan history.
- **MongoDB + GridFS** — optional; document binaries, extracted text, and AI summaries.

## Core Components

### 1. ScraperService (`app/services/scraper_service.py`)

Handles the low-level scraping operations:

- **`fetch_page()`**: Asynchronously fetches HTML content from URLs using aiohttp
- **`is_javascript_redirect()`**: Detects pages that use JavaScript redirects or have minimal content
- **`parse_links()`**: Extracts all links from HTML using BeautifulSoup
- **`filter_document_links()`**: Filters links based on file extension (PDF, DOC, etc.)
- **`get_file_info()`**: Retrieves file metadata via HTTP HEAD requests (size, accessibility)
- **`scan_page()`**: Combines the above to scan a single page for documents; automatically falls back to browser rendering if JavaScript is detected

### 2. BrowserService (`app/services/browser_service.py`)

Provides headless browser support for JavaScript-rendered pages:

- **`get_browser()`**: Returns a shared Playwright Chromium browser instance
- **`fetch_page_with_browser()`**: Fetches a page using headless browser, waiting for JavaScript to execute
- **`close_browser()`**: Closes the shared browser instance on shutdown

The browser service is automatically used when ScraperService detects:
- JavaScript redirects (e.g., `window.location`, `document.location`)
- Pages with minimal content and no links

### 3. CrawlerService (`app/services/crawler_service.py`)

Implements multi-page crawling with depth control:

- Uses breadth-first search (BFS) algorithm
- Tracks visited URLs to prevent infinite loops
- Filters to internal links only (same domain, with www/non-www normalization)
- Yields progress updates as an async generator for SSE streaming
- Respects rate limiting (configurable requests per second)
- Supports scan continuation (pause after page limit, resume from where it left off)
- Maintains `CrawlState` for session persistence between continuation scans

### 4. DownloadService (`app/services/download_service.py`)

Manages local-disk file downloads:

- Streams large files in chunks to avoid memory issues
- Handles filename conflicts by appending numbers
- Validates and sanitizes download paths
- Provides progress updates via async generator

### 5. MongoDBService (`app/services/mongodb_service.py`)

Stores downloaded documents in MongoDB GridFS for later search and AI summarization:

- `store_document()` writes bytes to GridFS and metadata to the `documents` collection
- Idempotent on `(source_url, scan_url)` — re-storing the same document updates rather than duplicates
- Text indexing (`text_search`) is created once at first write
- `test_connection()` returns structured diagnostics for the Settings page's "Test Connection" button
- Read APIs: `search_documents`, `get_document`, `get_document_file`, `delete_document`, `get_summary_stats`, `get_scan_summary_stats`, plus per-scan retry/reset helpers for failed summaries

### 6. TextExtractionService (`app/services/text_extraction_service.py`)

Extracts text from common document formats so it can be indexed and summarized:

- PDF via PyPDF2 with Stirling-PDF OCR fallback (image-only PDFs)
- DOCX via python-docx
- XLSX via openpyxl (also produces structured per-sheet metadata)
- PPTX via python-pptx
- Plain text and CSV directly

### 7. StirlingPDFService (`app/services/stirling_pdf_service.py`)

Wraps the optional Stirling-PDF container (`ghcr.io/stirling-tools/stirling-pdf`) used for OCR on image-only PDFs. Gracefully degrades to `None` returns when the container isn't reachable.

### 8. AISummarizationService (`app/services/ai_summarization_service.py`)

Background summarizer that calls a user-configured AI endpoint (Anthropic, OpenAI, or any OpenAI-compatible URL — auto-detected from the URL). Produces a title, short summary, full summary, keywords, and document-type classification per stored document. Retries transient errors with exponential backoff; marks unrecoverable failures so the user can review and reset them from the Documents page.

### 9. HistoryService (`app/services/history_service.py`)

Persists summary stats for every completed scan (URL, mode, depth, pages, doc count, duration, error counts, total / largest / smallest file sizes) into the SQLite `scan_history` table for display on the History page.

### 10. BackgroundTasks (`app/services/background_tasks.py`)

Tracks fire-and-forget asyncio tasks (notably the periodic session sweeper and async summarization triggers) with strong references so they aren't GC'd mid-flight. Cancelled cleanly during the FastAPI lifespan shutdown.

## API Endpoints

The complete, always-current OpenAPI schema is served at `http://localhost:8000/docs` (interactive Swagger UI) and `http://localhost:8000/openapi.json`. The README has a high-level grouping of endpoints; the live `/docs` is authoritative. The endpoint surface is too large to enumerate accurately in static documentation — past attempts drifted within weeks of new features landing.

Headline routes:

- **Scanning** — `POST /api/scrape/start`, `GET /api/scrape/progress/{id}` (SSE), `POST /api/scrape/continue/{id}`, `GET /api/scrape/retry/{id}` (SSE), `DELETE /api/scrape/cancel/{id}`
- **Local downloads** — `POST /api/download/validate-path`, `POST /api/download/start`, `GET /api/download/progress/{id}` (SSE)
- **MongoDB** — `POST /api/download/mongodb/start`, `GET /api/download/mongodb/progress/{id}` (SSE), `GET /api/download/mongodb/scans`, `GET /api/download/mongodb/search`, `GET /api/download/mongodb/export/csv`, plus per-scan summarize / retry-failed endpoints
- **Settings** — `GET/PUT /api/settings` (with `ai_api_key` and `mongodb_uri` masked in responses), `POST /api/settings/reset`
- **History** — `GET /api/history`, `DELETE /api/history`

## Data Flow

### Scraping Flow

1. User enters URL and options on the index page
2. Frontend POSTs to `/api/scrape/start`, receives session ID
3. Frontend opens SSE connection to `/api/scrape/progress/{id}`
4. Backend crawls pages, yielding `ScrapeProgress` events
5. On completion, backend yields `ScrapeResult` with all documents
6. Frontend redirects to `/results/{id}` to display documents

### Download Flow

1. User selects documents and specifies download path
2. Frontend validates path via `/api/download/validate-path`
3. Frontend POSTs to `/api/download/start` with selected URLs
4. Frontend opens SSE connection to `/api/download/progress/{id}`
5. Backend downloads files sequentially, yielding `DownloadProgress` events
6. On completion, shows summary of successful/failed downloads

## Configuration

### Default Settings (app/core/config.py)

Static defaults are defined in Pydantic settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `REQUEST_TIMEOUT` | 30s | HTTP request timeout |
| `MAX_CONCURRENT_REQUESTS` | 10 | Connection pool limit |
| `DEFAULT_CRAWL_DEPTH` | 2 | Default depth for "follow links" |
| `MAX_CRAWL_DEPTH` | 5 | Maximum allowed crawl depth |
| `MAX_PAGES_TO_SCAN` | 500 | Pages per scan batch (continuation available) |
| `REQUESTS_PER_SECOND` | 2.0 | Rate limiting for polite crawling |
| `MAX_FILE_SIZE_MB` | 100 | Maximum file size to download |
| `USER_AGENT` | Browser-like | User agent string for requests |

### Runtime Settings (Settings Page)

Users can configure settings through the web UI at `/settings`. These settings are persisted to SQLite and survive server restarts:

| Setting | Range | Description |
|---------|-------|-------------|
| Pages Per Scan Batch | 10-10,000 | Number of pages to scan before pausing |
| Request Timeout | 5-120s | How long to wait for each page |
| Rate Limit | 0.5-10 req/s | Maximum requests per second |
| Default Crawl Depth | 1-10 | Default depth for "Follow Internal Links" |
| Maximum Crawl Depth | 1-10 | Maximum depth users can select |

## Database

The application uses SQLite for persistent storage (`scraper.db`, at the repo root by default):

- **settings** — key/value store for user-configurable settings (JSON-encoded values). Includes secrets (`ai_api_key`, `mongodb_uri`); these are masked in API responses but stored as-is locally.
- **scan_history** — one row per completed scan, populated by `history_service.py`. Columns: `url`, `crawl_option`, `max_depth`, `scan_mode`, `document_filter`, `pages_scanned`, `documents_found`, `scan_error_count`, `document_error_count`, `duration_seconds`, `total_size_bytes`, `largest_file_name`/`size`, `smallest_file_name`/`size`, `started_at`, `completed_at`. The History page is a `SELECT … ORDER BY started_at DESC LIMIT N`, where N is the user-configurable `scan_history_limit`.

Document bytes, extracted text, and AI summaries live in MongoDB (`documents` collection + GridFS), not SQLite.

## Document Type Filters

Defined in `app/core/constants.py`:

| Filter | Extensions |
|--------|------------|
| Common Documents | PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, TXT, CSV, RTF, ODT |
| All Files | Common + ZIP, RAR, 7Z, JPG, PNG, GIF, MP3, MP4, and more |
| PDFs Only | PDF |

## Session Management

Sessions are stored in-memory using Python dictionaries:

- `scrape_sessions`: Tracks ongoing/completed scrape operations
- `download_sessions`: Tracks ongoing/completed downloads

Each session stores:
- Request parameters
- Current status
- Results (documents found)
- Cancellation flag

**Note**: For production use, consider replacing in-memory storage with Redis or a database for persistence and multi-instance support.

## Error Handling

The application handles various error conditions:

- **Network errors**: Logged and skipped, crawling continues
- **Timeouts**: Configurable timeout per request
- **Invalid URLs**: Validated before processing
- **Inaccessible files**: Marked with `is_accessible=False` in results
- **Path validation**: Checks write permissions before downloading

## Security Considerations

- **Path traversal protection**: Download paths are validated and sanitized
- **URL validation**: Ensures proper URL format before requests
- **Rate limiting**: Prevents overwhelming target servers
- **File size limits**: Configurable maximum file size
- **Filename sanitization**: Removes invalid characters from filenames

## Running the Application

The canonical entry point is `scripts/run.sh` (see the [README](../README.md) for what it does).
For a manual launch:

```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
# Access at http://localhost:8000
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_url_utils.py -v
```

## Key Features

### Automatic JavaScript Rendering

Many modern websites use JavaScript to load content dynamically. The scraper automatically detects when a page:
- Contains JavaScript redirects (`window.location`, `document.location`, etc.)
- Has minimal HTML content with few or no links

When detected, it automatically switches to Playwright headless browser to render the JavaScript and extract the actual content.

### Scan Continuation

For large websites, scanning can be paused after a configurable number of pages (default: 100). The user can then:
- Review documents found so far
- Continue scanning to find more pages
- The crawler maintains state (visited URLs, pending queue) between continuation scans

### Domain Normalization

URLs are normalized to treat `www.example.com` and `example.com` as the same domain, ensuring internal links are properly followed even when sites use inconsistent URL patterns.

### Real-time Progress

All operations provide real-time feedback via Server-Sent Events (SSE):
- Pages scanned count
- Documents found count
- Current operation status
- Download progress per file

## Future Improvements

Potential enhancements for production use:

1. **Persistent storage**: Replace in-memory sessions with Redis for multi-instance support
2. **Authentication**: Add user authentication for multi-user support
3. **Queue system**: Use Celery for background task processing
4. **Robots.txt**: Respect robots.txt directives
5. **Proxy support**: Add configurable proxy settings
6. **Export options**: Export document list to CSV/JSON
7. **Scheduling**: Add scheduled/recurring scans
8. **App container image**: Add a Dockerfile for the app itself (Stirling-PDF already runs in a container; the app does not)
9. **Custom headers**: Allow users to specify custom HTTP headers
10. **Cookie support**: Handle sites that require authentication cookies
