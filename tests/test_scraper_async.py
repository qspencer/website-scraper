"""Tests for async methods in ScraperService."""

import pytest
import asyncio
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.scraper_service import ScraperService
from app.core.constants import DocumentTypeFilter
from app.schemas.document import DocumentInfo


def mock_response_cm(response=None, error=None):
    """Create an async context manager that yields a response or raises."""
    cm = AsyncMock()
    if error:
        cm.__aenter__.side_effect = error
    else:
        cm.__aenter__.return_value = response
    cm.__aexit__.return_value = False
    return cm


def make_response(status=200, headers=None, text="", content_iter=None):
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.text = AsyncMock(return_value=text)
    if content_iter is not None:
        resp.content = MagicMock()
        resp.content.iter_chunked = content_iter
    return resp


def make_session(get_resp=None, head_resp=None, get_error=None, head_error=None):
    """Create a mock aiohttp session."""
    session = MagicMock()
    if get_resp is not None or get_error is not None:
        session.get.return_value = mock_response_cm(get_resp, get_error)
    if head_resp is not None or head_error is not None:
        session.head.return_value = mock_response_cm(head_resp, head_error)
    return session


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


class TestFetchPage:
    async def test_success(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><body>Hello</body></html>",
        )
        session = make_session(get_resp=resp)

        html, error = await scraper.fetch_page(session, "https://example.com")
        assert html == "<html><body>Hello</body></html>"
        assert error is None

    async def test_non_200(self):
        scraper = ScraperService()
        resp = make_response(status=404, headers={"Content-Type": "text/html"})
        session = make_session(get_resp=resp)

        html, error = await scraper.fetch_page(session, "https://example.com/missing")
        assert html is None
        assert "404" in error

    async def test_non_html_content_type(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={"Content-Type": "application/pdf"},
            text="binary content",
        )
        session = make_session(get_resp=resp)

        html, error = await scraper.fetch_page(session, "https://example.com/file.pdf")
        assert html is None
        assert "HTML" in error or "html" in error.lower()

    async def test_timeout(self):
        scraper = ScraperService()
        session = make_session(get_error=asyncio.TimeoutError())

        html, error = await scraper.fetch_page(session, "https://example.com")
        assert html is None
        assert "timed out" in error.lower()

    async def test_connection_error(self):
        scraper = ScraperService()
        session = make_session(get_error=aiohttp.ClientConnectorError(
            connection_key=MagicMock(), os_error=OSError("Connection refused")
        ))

        html, error = await scraper.fetch_page(session, "https://example.com")
        assert html is None
        assert "onnection" in error  # "Connection error" or similar

    async def test_xhtml_content_type_accepted(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={"Content-Type": "application/xhtml+xml"},
            text="<html><body>XHTML</body></html>",
        )
        session = make_session(get_resp=resp)

        html, error = await scraper.fetch_page(session, "https://example.com")
        assert html == "<html><body>XHTML</body></html>"
        assert error is None


# ---------------------------------------------------------------------------
# get_file_info
# ---------------------------------------------------------------------------


class TestGetFileInfo:
    async def test_success_with_size(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={"Content-Length": "1048576"},
        )
        session = make_session(head_resp=resp)

        doc = await scraper.get_file_info(
            session, "https://example.com/report.pdf", "https://example.com", 0
        )
        assert isinstance(doc, DocumentInfo)
        assert doc.is_accessible is True
        assert doc.file_size_bytes == 1048576
        assert doc.filename == "report.pdf"
        assert doc.extension == ".pdf"

    async def test_success_no_content_length(self):
        scraper = ScraperService()
        resp = make_response(status=200, headers={})
        session = make_session(head_resp=resp)

        doc = await scraper.get_file_info(
            session, "https://example.com/report.pdf", "https://example.com", 0
        )
        assert doc.is_accessible is True
        assert doc.file_size_bytes is None

    async def test_content_disposition_filename(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={
                "Content-Length": "100",
                "Content-Disposition": 'attachment; filename="actual_name.pdf"',
            },
        )
        session = make_session(head_resp=resp)

        doc = await scraper.get_file_info(
            session, "https://example.com/download?id=123", "https://example.com", 0
        )
        assert doc.filename == "actual_name.pdf"

    async def test_non_200_marks_inaccessible(self):
        scraper = ScraperService()
        resp = make_response(status=403, headers={})
        session = make_session(head_resp=resp)

        with patch("app.services.scraper_service.log_inaccessible") as mock_log:
            doc = await scraper.get_file_info(
                session, "https://example.com/secret.pdf", "https://example.com", 0
            )
        assert doc.is_accessible is False
        assert "403" in doc.error_message
        mock_log.assert_called_once()

    async def test_timeout_marks_inaccessible(self):
        scraper = ScraperService()
        session = make_session(head_error=asyncio.TimeoutError())

        with patch("app.services.scraper_service.log_inaccessible") as mock_log:
            doc = await scraper.get_file_info(
                session, "https://example.com/slow.pdf", "https://example.com", 0
            )
        assert doc.is_accessible is False
        assert doc.error_message == "Timeout"
        mock_log.assert_called_once()

    async def test_generic_error_marks_inaccessible(self):
        scraper = ScraperService()
        session = make_session(head_error=Exception("DNS failure"))

        with patch("app.services.scraper_service.log_inaccessible"):
            doc = await scraper.get_file_info(
                session, "https://example.com/file.pdf", "https://example.com", 0
            )
        assert doc.is_accessible is False
        assert "DNS failure" in doc.error_message


# ---------------------------------------------------------------------------
# get_file_size_via_get
# ---------------------------------------------------------------------------


class TestGetFileSizeViaGet:
    async def test_206_partial_content(self):
        scraper = ScraperService()
        resp = make_response(
            status=206,
            headers={"Content-Range": "bytes 0-0/524288"},
        )
        session = make_session(get_resp=resp)

        size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size == 524288

    async def test_200_with_content_length(self):
        scraper = ScraperService()
        resp = make_response(
            status=200,
            headers={"Content-Length": "2048"},
        )
        session = make_session(get_resp=resp)

        size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size == 2048

    async def test_200_no_content_length(self):
        scraper = ScraperService()
        resp = make_response(status=200, headers={})
        session = make_session(get_resp=resp)

        size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size is None

    async def test_unexpected_status(self):
        scraper = ScraperService()
        resp = make_response(status=403, headers={})
        session = make_session(get_resp=resp)

        with patch("app.services.scraper_service.log_inaccessible"):
            size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size is None

    async def test_timeout(self):
        scraper = ScraperService()
        session = make_session(get_error=asyncio.TimeoutError())

        with patch("app.services.scraper_service.log_inaccessible"):
            size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size is None

    async def test_invalid_content_range(self):
        scraper = ScraperService()
        resp = make_response(
            status=206,
            headers={"Content-Range": "bytes 0-0/*"},  # Unknown size
        )
        session = make_session(get_resp=resp)

        size = await scraper.get_file_size_via_get(session, "https://example.com/file.pdf")
        assert size is None


# ---------------------------------------------------------------------------
# scan_page
# ---------------------------------------------------------------------------


class TestScanPage:
    async def test_normal_page_with_documents(self):
        scraper = ScraperService()
        html = """
        <html><body>
            <a href="https://example.com/files/report.pdf">PDF</a>
            <a href="https://example.com/page2.html">Page 2</a>
        </body></html>
        """
        get_resp = make_response(
            status=200,
            headers={"Content-Type": "text/html"},
            text=html,
        )
        head_resp = make_response(status=200, headers={"Content-Length": "5000"})

        session = make_session(get_resp=get_resp, head_resp=head_resp)

        page_links, documents, warning = await scraper.scan_page(
            session, "https://example.com", DocumentTypeFilter.COMMON, 0
        )
        assert len(documents) == 1
        assert documents[0].url == "https://example.com/files/report.pdf"
        assert "page2" in page_links[0]
        assert warning is None

    async def test_fetch_error_returns_empty(self):
        scraper = ScraperService()
        resp = make_response(status=500, headers={"Content-Type": "text/html"})
        session = make_session(get_resp=resp)

        page_links, documents, warning = await scraper.scan_page(
            session, "https://example.com", DocumentTypeFilter.COMMON, 0
        )
        assert page_links == []
        assert documents == []
        assert warning is not None

    async def test_js_redirect_uses_browser(self):
        scraper = ScraperService()
        js_html = "<html><script>window.location='https://example.com/real';</script></html>"
        browser_html = """
        <html><body>
            <a href="https://example.com/doc.pdf">Doc</a>
        </body></html>
        """
        get_resp = make_response(
            status=200,
            headers={"Content-Type": "text/html"},
            text=js_html,
        )
        head_resp = make_response(status=200, headers={"Content-Length": "1000"})
        session = make_session(get_resp=get_resp, head_resp=head_resp)

        with patch(
            "app.services.scraper_service.fetch_page_with_browser",
            new=AsyncMock(return_value=(browser_html, None)),
        ):
            page_links, documents, warning = await scraper.scan_page(
                session, "https://example.com", DocumentTypeFilter.COMMON, 0
            )
        assert len(documents) == 1
        assert documents[0].url == "https://example.com/doc.pdf"

    async def test_page_no_documents(self):
        scraper = ScraperService()
        html = """
        <html><body>
            <a href="https://example.com/about">About</a>
            <a href="https://example.com/contact">Contact</a>
        </body></html>
        """
        get_resp = make_response(
            status=200,
            headers={"Content-Type": "text/html"},
            text=html,
        )
        session = make_session(get_resp=get_resp)

        page_links, documents, warning = await scraper.scan_page(
            session, "https://example.com", DocumentTypeFilter.COMMON, 0
        )
        assert len(documents) == 0
        assert len(page_links) == 2


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    async def test_returns_session(self):
        scraper = ScraperService()
        session = await scraper.create_session()
        assert isinstance(session, aiohttp.ClientSession)
        await session.close()
