# Website Document Scraper

A web application that scans websites for downloadable documents, displays a preview list, and allows batch downloading to a local directory or MongoDB database with optional AI-powered summaries.

## Features

- **Document Discovery**: Automatically finds linked documents (PDFs, Word, Excel, etc.) on web pages
- **Multi-page Crawling**: Follow internal links with configurable depth, batch or continuous scanning modes, and autoscan
- **Document Type Filtering**:
  - Common Documents (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, TXT, CSV, RTF, ODT)
  - All Files (includes archives, images, media, and more)
  - PDFs Only
- **Real-time Progress**: Live updates via Server-Sent Events during scanning and downloading
- **Batch Downloads**: Select files and download to a local directory or store in MongoDB
- **MongoDB Storage**: Store documents in MongoDB with GridFS, automatic text extraction (PDF, DOCX, XLSX, TXT), and full-text search
- **AI Summarization**: Background summarization of stored documents via a configurable AI API (Anthropic, OpenAI, or any compatible endpoint)
- **Scan History**: Browse and review past scans with detailed statistics
- **Retry Failed Items**: Retry inaccessible pages and documents after a scan completes
- **URL Validation**: Friendly error messages for invalid or malformed URLs
- **File Size Detection**: Displays file sizes with option to calculate unknown sizes
- **JavaScript Rendering**: Automatically uses headless browser for JavaScript-heavy pages
- **Configurable Settings**: Adjust timeouts, concurrency, rate limiting, crawl depth, and more via the settings page
- **Duplicate Detection**: Skips duplicate files based on URL and filename

## Tech Stack

- **Backend**: FastAPI (Python 3.12+)
- **HTTP Client**: aiohttp (async)
- **HTML Parsing**: BeautifulSoup4 with lxml
- **Browser Automation**: Playwright (for JavaScript rendering)
- **Frontend**: Jinja2 templates + Tailwind CSS
- **Real-time Updates**: Server-Sent Events (sse-starlette)
- **Validation**: Pydantic
- **Database**: SQLite (settings and scan history)
- **Document Storage**: MongoDB with GridFS (optional)
- **Text Extraction**: PyPDF2, python-docx, openpyxl

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

5. (Optional) Install MongoDB for document storage:
   See the [MongoDB installation guide](https://www.mongodb.com/docs/manual/installation/).

## Usage

1. Activate the virtual environment and start the server:
   ```bash
   source venv/bin/activate        # Linux/macOS
   # or
   venv\Scripts\activate           # Windows

   uvicorn app.main:app --reload --port 8001
   ```

2. Open your browser to `http://localhost:8001`

3. Enter a URL to scan, select a document type filter, and choose scan depth

4. Review discovered documents and select files to download

5. Choose a download destination (file system or MongoDB) and start the download

For a detailed walkthrough of every feature, see the [User Guide](docs/user-guide.md).

## Configuration

Access the settings page at `http://localhost:8001/settings` to configure:

### Scan Performance

| Setting | Description | Default |
|---------|-------------|---------|
| Pages Per Scan Batch | Pages to scan before pausing in batch mode | 500 |
| Request Timeout | Timeout for HTTP requests (seconds) | 30 |
| Rate Limit | Maximum requests per second | 2.0 |
| Concurrent Requests | Maximum parallel requests | 10 |

### Crawl Configuration

| Setting | Description | Default |
|---------|-------------|---------|
| Default Crawl Depth | Starting depth for "Follow Internal Links" | 2 |
| Maximum Crawl Depth | Maximum selectable crawl depth | 5 |
| Scan History Limit | Number of past scans to keep | 20 |

### MongoDB Storage (Optional)

| Setting | Description | Default |
|---------|-------------|---------|
| Connection URI | MongoDB connection string | `mongodb://localhost:27017` |
| Database Name | Database for document storage | `document_scraper` |

### AI Summarization (Optional)

| Setting | Description | Default |
|---------|-------------|---------|
| API URL | AI service endpoint | (empty) |
| API Key | Authentication key | (empty) |
| Model | Model to use for summaries | (empty) |

## Project Structure

```
website-scraper/
├── app/
│   ├── main.py                          # FastAPI application entry point
│   ├── api/routes/
│   │   ├── scraper.py                   # Scanning and retry endpoints
│   │   ├── downloads.py                 # Download, MongoDB, and summarization endpoints
│   │   ├── settings.py                  # Settings endpoints
│   │   └── history.py                   # Scan history endpoints
│   ├── core/
│   │   ├── config.py                    # Application settings
│   │   ├── constants.py                 # File type definitions
│   │   ├── database.py                  # SQLite settings/history storage
│   │   └── logging_config.py            # Logging configuration
│   ├── schemas/
│   │   ├── document.py                  # Document data models
│   │   └── scrape.py                    # Request/response models
│   ├── services/
│   │   ├── scraper_service.py           # Core scraping logic
│   │   ├── crawler_service.py           # Multi-page crawling
│   │   ├── download_service.py          # File system downloads
│   │   ├── browser_service.py           # Headless browser support
│   │   ├── settings_service.py          # Runtime settings (singleton)
│   │   ├── history_service.py           # Scan history persistence
│   │   ├── mongodb_service.py           # MongoDB/GridFS document storage
│   │   ├── text_extraction_service.py   # PDF/DOCX/XLSX/TXT text extraction
│   │   └── ai_summarization_service.py  # Background AI summarization
│   ├── templates/
│   │   ├── base.html                    # Base template with navigation
│   │   ├── index.html                   # Main scan form
│   │   ├── results.html                 # Document list and downloads
│   │   ├── documents.html               # Document search and browse
│   │   ├── settings.html                # Settings page
│   │   └── history.html                 # Scan history page
│   └── utils/
│       ├── url_utils.py                 # URL validation/normalization
│       └── file_utils.py                # File size/path utilities
├── tests/                               # Test suite (391 tests)
├── docs/                                # Documentation
│   ├── architecture.md                  # Technical architecture
│   └── user-guide.md                    # Non-technical user guide
├── logs/                                # Log files (created at runtime)
├── requirements.txt
└── README.md
```

## API Endpoints

### Pages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main scan form |
| GET | `/results/{session_id}` | Results page |
| GET | `/documents` | Document search and browse |
| GET | `/settings` | Settings page |
| GET | `/history` | Scan history page |
| GET | `/health` | Health check |

### Scanning

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scrape/start` | Start a new scan |
| GET | `/api/scrape/progress/{id}` | SSE stream for scan progress |
| GET | `/api/scrape/results/{id}` | Get scan results |
| GET | `/api/scrape/summary/{id}` | Get scan summary stats |
| POST | `/api/scrape/continue/{id}` | Continue a paused scan |
| POST | `/api/scrape/retry/{id}` | Retry failed pages and documents |
| POST | `/api/scrape/calculate-sizes/{id}` | Calculate unknown file sizes |
| DELETE | `/api/scrape/cancel/{id}` | Cancel a running scan |
| DELETE | `/api/scrape/session/{id}` | Clean up session data |

### Downloads

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/download/start` | Start file system download |
| GET | `/api/download/progress/{id}` | SSE stream for download progress |
| POST | `/api/download/validate-path` | Validate download directory |
| POST | `/api/download/create-directory` | Create download directory |
| DELETE | `/api/download/cancel/{id}` | Cancel a download |
| DELETE | `/api/download/session/{id}` | Clean up session data |

### MongoDB

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/download/mongodb/status` | Test MongoDB connection |
| POST | `/api/download/mongodb/start` | Start MongoDB download |
| GET | `/api/download/mongodb/progress/{id}` | SSE stream for MongoDB download |
| GET | `/api/download/mongodb/scans` | List stored scans with document counts |
| GET | `/api/download/mongodb/search` | Search documents (query, scan_url, extension) |
| GET | `/api/download/mongodb/extensions` | Get file extensions (optionally by scan) |
| GET | `/api/download/mongodb/document/{id}` | Get full document metadata |
| DELETE | `/api/download/mongodb/document/{id}` | Delete a document |
| GET | `/api/download/mongodb/summarization/status` | Get summarization status and stats |
| POST | `/api/download/mongodb/summarization/start` | Trigger summarization manually |
| POST | `/api/download/mongodb/summarization/retry` | Retry failed summaries |

### Settings & History

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get current settings |
| PUT | `/api/settings` | Update settings |
| POST | `/api/settings/reset` | Reset settings to defaults |
| GET | `/api/history` | Get scan history |
| DELETE | `/api/history` | Clear scan history |

## Testing

Run the test suite (activate the virtual environment first, or use the full path):
```bash
source venv/bin/activate
pytest tests/ -v
```

Run with coverage:
```bash
pytest tests/ --cov=app --cov-report=term-missing
```

## Logging

Logs are written to the `logs/` directory:
- `scraper.log` - Main application log (rotates at 10MB, keeps 5 backups)

## License

MIT License
