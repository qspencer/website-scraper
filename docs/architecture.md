# Document Scraper - Architecture Documentation

## Overview

Document Scraper is a Python web application that scans websites for linked documents, displays a preview of found files, and allows users to download selected documents to a specified location.

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
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── core/
│   │   ├── config.py           # Application settings (Pydantic)
│   │   ├── constants.py        # File extensions, enums, display names
│   │   ├── database.py         # SQLite database for persistence
│   │   └── logging_config.py   # Centralized logging configuration
│   ├── api/
│   │   └── routes/
│   │       ├── scraper.py      # Scraping API endpoints
│   │       ├── downloads.py    # Download API endpoints
│   │       └── settings.py     # Settings API endpoints
│   ├── schemas/
│   │   ├── document.py         # DocumentInfo, DownloadProgress models
│   │   └── scrape.py           # Request/response models
│   ├── services/
│   │   ├── scraper_service.py  # Core scraping logic
│   │   ├── crawler_service.py  # Multi-page crawling
│   │   ├── browser_service.py  # Playwright browser for JS rendering
│   │   ├── download_service.py # File download handling
│   │   └── settings_service.py # Runtime settings with persistence
│   ├── utils/
│   │   ├── url_utils.py        # URL validation, normalization
│   │   └── file_utils.py       # File size formatting, path validation
│   └── templates/
│       ├── base.html           # Base template with Tailwind
│       ├── index.html          # Main scraping interface
│       ├── results.html        # Document preview and download
│       └── settings.html       # Settings configuration page
├── tests/
│   ├── test_url_utils.py       # URL utility tests
│   ├── test_file_utils.py      # File utility tests
│   ├── test_scraper_service.py # Scraper service tests
│   └── test_api.py             # API endpoint tests
├── static/
│   └── js/
│       └── app.js              # Shared JavaScript utilities
├── docs/
│   └── architecture.md         # This document
├── requirements.txt
└── venv/                       # Virtual environment
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web Browser                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │  index.html │───▶│ results.html│───▶│  Download Files     │  │
│  │  (Input)    │    │  (Preview)  │    │                     │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
└────────────┬────────────────┬────────────────────┬──────────────┘
             │                │                    │
             │ SSE            │ SSE                │ SSE
             ▼                ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     API Routes                            │   │
│  │  POST /api/scrape/start      GET /api/scrape/progress     │   │
│  │  GET  /api/scrape/results    POST /api/download/start     │   │
│  │  GET  /api/download/progress POST /api/download/validate  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌───────────────────────────┴───────────────────────────────┐  │
│  │                      Services Layer                        │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │  │
│  │  │  Scraper    │  │  Crawler    │  │   Download      │    │  │
│  │  │  Service    │  │  Service    │  │   Service       │    │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      External Websites                           │
│                  (Target URLs to scrape)                         │
└─────────────────────────────────────────────────────────────────┘
```

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

Manages file downloads:

- Streams large files in chunks to avoid memory issues
- Handles filename conflicts by appending numbers
- Validates and sanitizes download paths
- Provides progress updates via async generator

## API Endpoints

### Scraping

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scrape/start` | Initiates a scrape session, returns session ID |
| POST | `/api/scrape/continue/{id}` | Continues a paused scan from where it left off |
| GET | `/api/scrape/progress/{id}` | SSE stream of real-time progress updates |
| GET | `/api/scrape/results/{id}` | Returns final list of found documents |
| DELETE | `/api/scrape/cancel/{id}` | Cancels an ongoing scrape |

### Downloads

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/download/validate-path` | Validates a download directory path |
| POST | `/api/download/start` | Starts downloading selected documents |
| GET | `/api/download/progress/{id}` | SSE stream of download progress |
| DELETE | `/api/download/cancel/{id}` | Cancels ongoing downloads |

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

The application uses SQLite for persistent storage (`scraper.db`):

- **settings**: Key-value store for user-configurable settings
- **scan_history**: (Reserved for future use) Track scan history

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

```bash
# Navigate to project directory
cd /home/ubuntu/Dev/website-scraper

# Activate virtual environment
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt
playwright install chromium

# Start development server
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Access at http://localhost:8001
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
8. **Docker**: Add Dockerfile for containerized deployment
9. **Custom headers**: Allow users to specify custom HTTP headers
10. **Cookie support**: Handle sites that require authentication cookies
