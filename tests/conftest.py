import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# --- Redirect database to a temp file BEFORE any app code touches it ---
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()

import app.core.database as _db_module
_db_module.DB_PATH = _tmp_db_path
_db_module.init_database()

# --- Redirect logging to a temp dir BEFORE app.main imports ---
# app.main calls setup_logging() at import time. Patch the module attribute first
# so the from-import in app.main binds to our temp-dir version — this keeps even
# the import-time lines out of the real logs/scraper.log (closes R-TEST-5 fully).
import tempfile as _tempfile
import app.core.logging_config as _logcfg
_tmp_log_dir = _tempfile.mkdtemp(prefix="scraper-test-logs-")
_orig_setup = _logcfg.setup_logging
def _temp_setup(level="INFO", log_dir="logs"):
    return _orig_setup(level="WARNING", log_dir=_tmp_log_dir)
_logcfg.setup_logging = _temp_setup

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _close_mongo_client_on_teardown():
    """Close the module-level pymongo client before pytest tears down logging.

    Without this, the pymongo SDAM monitor thread keeps polling after the test
    session ends and tries to emit debug records into a closed log handler,
    producing the noisy 'ValueError: I/O operation on closed file' traceback
    that previously appeared after every run.
    """
    yield
    try:
        from app.services import mongodb_service
        client = getattr(mongodb_service, "_client", None)
        if client is not None:
            client.close()
            mongodb_service._client = None
    except Exception:
        pass


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def sample_html():
    """Sample HTML with various link types."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Test Page</title></head>
    <body>
        <a href="/documents/report.pdf">PDF Report</a>
        <a href="https://example.com/files/data.xlsx">Excel File</a>
        <a href="/page2.html">Another Page</a>
        <a href="document.docx">Word Doc</a>
        <a href="javascript:void(0)">JS Link</a>
        <a href="mailto:test@example.com">Email</a>
        <a href="#section">Anchor</a>
        <a href="/images/photo.jpg">Image</a>
    </body>
    </html>
    """


@pytest.fixture
def sample_html_no_docs():
    """Sample HTML with no document links."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Test Page</title></head>
    <body>
        <a href="/page1.html">Page 1</a>
        <a href="/page2.html">Page 2</a>
        <a href="https://example.com/">Home</a>
    </body>
    </html>
    """
