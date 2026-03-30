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

from app.main import app


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
