# Website Document Scraper

A web application that scans websites for downloadable documents, displays a preview list, and allows batch downloading to a specified location.

## Features

- **Document Discovery**: Automatically finds linked documents (PDFs, Word, Excel, etc.) on web pages
- **Multi-page Crawling**: Option to follow internal links and scan multiple pages with configurable depth
- **Document Type Filtering**:
  - Common Documents (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, TXT, CSV, RTF, ODT)
  - All Files (includes archives, images, media, and more)
  - PDFs Only
- **Real-time Progress**: Live updates via Server-Sent Events during scanning and downloading
- **Batch Downloads**: Select multiple files and download them all to a chosen directory
- **File Size Detection**: Displays file sizes with option to calculate unknown sizes
- **JavaScript Rendering**: Automatically uses headless browser for JavaScript-heavy pages
- **Configurable Settings**: Adjust request timeouts, concurrency limits, and rate limiting
- **Duplicate Detection**: Skips duplicate files based on URL and filename
- **Inaccessible File Logging**: Tracks files that couldn't be accessed

## Tech Stack

- **Backend**: FastAPI (Python 3.12+)
- **HTTP Client**: aiohttp (async)
- **HTML Parsing**: BeautifulSoup4 with lxml
- **Browser Automation**: Playwright (for JavaScript rendering)
- **Frontend**: Jinja2 templates + Tailwind CSS
- **Real-time Updates**: Server-Sent Events (sse-starlette)
- **Validation**: Pydantic

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd website-scraper
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/macOS
   # or
   venv\Scripts\activate     # Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Install Playwright browsers (for JavaScript rendering):
   ```bash
   playwright install chromium
   ```

## Usage

1. Start the server:
   ```bash
   uvicorn app.main:app --reload --port 8001
   ```

2. Open your browser to `http://localhost:8001`

3. Enter a URL to scan, select document type filter, and choose scan depth

4. Review discovered documents and select files to download

5. Specify download location and start the batch download

## Configuration

Access the settings page at `http://localhost:8001/settings` to configure:

| Setting | Description | Default |
|---------|-------------|---------|
| Request Timeout | Timeout for HTTP requests (seconds) | 30 |
| Max Concurrent Requests | Maximum parallel requests | 10 |
| Requests Per Second | Rate limit for requests | 5 |
| Max Crawl Depth | Maximum depth when following links | 3 |
| Max Pages Per Scan | Batch size before pausing | 100 |

## Project Structure

```
website-scraper/
├── app/
│   ├── main.py                 # FastAPI application entry point
│   ├── api/routes/
│   │   ├── scraper.py          # Scraping endpoints
│   │   ├── downloads.py        # Download endpoints
│   │   └── settings.py         # Settings endpoints
│   ├── core/
│   │   ├── config.py           # Application settings
│   │   ├── constants.py        # File type definitions
│   │   ├── database.py         # SQLite settings storage
│   │   └── logging_config.py   # Logging configuration
│   ├── schemas/
│   │   ├── document.py         # Document data models
│   │   └── scrape.py           # Request/response models
│   ├── services/
│   │   ├── scraper_service.py  # Core scraping logic
│   │   ├── crawler_service.py  # Multi-page crawling
│   │   ├── download_service.py # File downloads
│   │   ├── browser_service.py  # Headless browser support
│   │   └── settings_service.py # Runtime settings
│   ├── templates/
│   │   ├── base.html           # Base template
│   │   ├── index.html          # Main scan form
│   │   ├── results.html        # Document list and downloads
│   │   └── settings.html       # Settings page
│   └── utils/
│       ├── url_utils.py        # URL validation/normalization
│       └── file_utils.py       # File size/path utilities
├── tests/                      # Test suite
├── logs/                       # Log files (created at runtime)
├── requirements.txt
└── README.md
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main scan form |
| GET | `/results/{session_id}` | Results page |
| GET | `/settings` | Settings page |
| POST | `/api/scrape/start` | Start a new scan |
| GET | `/api/scrape/progress/{id}` | SSE stream for scan progress |
| GET | `/api/scrape/results/{id}` | Get scan results |
| POST | `/api/scrape/continue/{id}` | Continue a paused scan |
| DELETE | `/api/scrape/cancel/{id}` | Cancel a scan |
| POST | `/api/download/start` | Start batch download |
| GET | `/api/download/progress/{id}` | SSE stream for download progress |
| POST | `/api/download/validate-path` | Validate download path |
| GET | `/api/settings` | Get current settings |
| PUT | `/api/settings` | Update settings |

## Testing

Run the test suite:
```bash
pytest tests/ -v
```

Run with coverage:
```bash
pytest tests/ --cov=app --cov-report=term-missing
```

## Logging

Logs are written to the `logs/` directory:
- `scraper.log` - Main application log (rotates at 10MB, keeps 5 backups)
- `inaccessible_documents.log` - URLs that couldn't be accessed (cleared each scan)

## License

MIT License
