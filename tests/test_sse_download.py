"""Tests for SSE progress streaming in download routes."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.api.routes.scraper import scrape_sessions
from app.api.routes.downloads import download_sessions
from app.schemas.scrape import ScrapeRequest, ScrapeResult
from app.schemas.document import DocumentInfo, DownloadProgress
from app.core.constants import DocumentTypeFilter, CrawlDepthOption


def _make_doc(url="https://example.com/file.pdf"):
    return DocumentInfo(
        url=url,
        filename=url.rsplit("/", 1)[-1],
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
    )


def _setup_download_session(session_id="test-dl"):
    """Create a download session entry."""
    docs = [_make_doc("https://example.com/a.pdf"), _make_doc("https://example.com/b.pdf")]
    download_sessions[session_id] = {
        "documents": docs,
        "download_path": "/tmp",
        "status": "pending",
        "cancelled": False,
    }
    return session_id, docs


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    download_sessions.clear()
    scrape_sessions.clear()


# ---------------------------------------------------------------------------
# download_progress SSE endpoint
# ---------------------------------------------------------------------------


class TestDownloadProgressSSE:
    async def test_progress_and_complete(self, client):
        """Normal flow: downloading events then complete."""
        session_id, docs = _setup_download_session()

        async def mock_download_batch(documents, download_path):
            for i, doc in enumerate(documents):
                yield DownloadProgress(
                    status="downloading",
                    current_file=doc.filename,
                    current_file_index=i + 1,
                    total_files=len(documents),
                    files_completed=i,
                    message=f"Downloading {doc.filename}...",
                )
            yield DownloadProgress(
                status="complete",
                total_files=len(documents),
                files_completed=len(documents),
                files_failed=0,
                message="All done",
            )

        with patch("app.api.routes.downloads.download_service") as mock_ds:
            mock_ds.download_batch = mock_download_batch
            response = client.get(f"/api/download/progress/{session_id}")

        assert response.status_code == 200
        body = response.text
        assert "event: downloading" in body
        assert "event: complete" in body
        assert download_sessions[session_id]["status"] == "complete"

    async def test_cancellation(self, client):
        """Download cancelled mid-stream yields cancelled event."""
        session_id, docs = _setup_download_session()
        # Pre-cancel so the first iteration catches it
        download_sessions[session_id]["cancelled"] = True

        async def mock_download_batch(documents, download_path):
            yield DownloadProgress(
                status="downloading",
                current_file="a.pdf",
                current_file_index=1,
                total_files=2,
                message="Downloading...",
            )
            yield DownloadProgress(
                status="complete",
                total_files=2,
                files_completed=2,
                message="Done",
            )

        with patch("app.api.routes.downloads.download_service") as mock_ds:
            mock_ds.download_batch = mock_download_batch
            response = client.get(f"/api/download/progress/{session_id}")

        body = response.text
        assert "event: cancelled" in body

    async def test_error_during_download(self, client):
        """Exception in download_batch yields error event."""
        session_id, docs = _setup_download_session()

        async def mock_download_batch(documents, download_path):
            raise RuntimeError("Disk full")
            yield  # noqa: E501

        with patch("app.api.routes.downloads.download_service") as mock_ds:
            mock_ds.download_batch = mock_download_batch
            response = client.get(f"/api/download/progress/{session_id}")

        body = response.text
        assert "event: error" in body
        assert "Disk full" in body
        assert download_sessions[session_id]["status"] == "error"

    async def test_not_found_session(self, client):
        response = client.get("/api/download/progress/nonexistent-id")
        assert response.status_code == 404

    async def test_error_event_from_service(self, client):
        """Service yields an error DownloadProgress event."""
        session_id, docs = _setup_download_session()

        async def mock_download_batch(documents, download_path):
            yield DownloadProgress(
                status="error",
                message="Cannot create download directory",
            )

        with patch("app.api.routes.downloads.download_service") as mock_ds:
            mock_ds.download_batch = mock_download_batch
            response = client.get(f"/api/download/progress/{session_id}")

        body = response.text
        assert "event: error" in body
        assert "Cannot create download directory" in body
