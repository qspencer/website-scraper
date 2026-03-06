"""Tests for the download service."""

import pytest
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.download_service import DownloadService
from app.schemas.document import DocumentInfo, DownloadProgress


def mock_response_cm(response=None, error=None):
    """Create an async context manager for session.get()."""
    cm = AsyncMock()
    if error:
        cm.__aenter__.side_effect = error
    else:
        cm.__aenter__.return_value = response
    cm.__aexit__.return_value = False
    return cm


def make_stream_response(status=200, data=b"file content"):
    """Create a mock aiohttp response with streaming content."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Length": str(len(data))}

    async def iter_chunked(size):
        for i in range(0, len(data), size):
            yield data[i : i + size]

    resp.content = MagicMock()
    resp.content.iter_chunked = iter_chunked
    return resp


def make_doc(url, filename=None):
    if filename is None:
        filename = url.rsplit("/", 1)[-1]
    return DocumentInfo(
        url=url,
        filename=filename,
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
    )


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    async def test_success(self, tmp_path):
        service = DownloadService()
        data = b"PDF file content here"
        resp = make_stream_response(status=200, data=data)
        session = MagicMock()
        session.get.return_value = mock_response_cm(resp)

        success, message, filename = await service.download_file(
            session, "https://example.com/report.pdf", str(tmp_path)
        )

        assert success is True
        assert filename == "report.pdf"
        assert (tmp_path / "report.pdf").read_bytes() == data

    async def test_custom_filename(self, tmp_path):
        service = DownloadService()
        resp = make_stream_response(status=200, data=b"data")
        session = MagicMock()
        session.get.return_value = mock_response_cm(resp)

        success, message, filename = await service.download_file(
            session, "https://example.com/download?id=1", str(tmp_path), "custom.pdf"
        )

        assert success is True
        assert filename == "custom.pdf"
        assert (tmp_path / "custom.pdf").exists()

    async def test_non_200_status(self, tmp_path):
        service = DownloadService()
        resp = MagicMock()
        resp.status = 404
        session = MagicMock()
        session.get.return_value = mock_response_cm(resp)

        success, message, filename = await service.download_file(
            session, "https://example.com/missing.pdf", str(tmp_path)
        )

        assert success is False
        assert "404" in message
        assert filename is None

    async def test_timeout_cleans_partial_file(self, tmp_path):
        service = DownloadService()
        session = MagicMock()
        session.get.return_value = mock_response_cm(error=asyncio.TimeoutError())

        success, message, filename = await service.download_file(
            session, "https://example.com/large.pdf", str(tmp_path)
        )

        assert success is False
        assert "timed out" in message.lower()
        # No partial file left behind
        assert len(list(tmp_path.iterdir())) == 0

    async def test_connection_error(self, tmp_path):
        service = DownloadService()
        session = MagicMock()
        import aiohttp
        session.get.return_value = mock_response_cm(
            error=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("refused")
            )
        )

        success, message, filename = await service.download_file(
            session, "https://example.com/file.pdf", str(tmp_path)
        )

        assert success is False
        assert "onnection" in message

    async def test_unique_filename_on_conflict(self, tmp_path):
        service = DownloadService()
        # Pre-create a file with the same name
        (tmp_path / "report.pdf").write_bytes(b"existing")

        resp = make_stream_response(status=200, data=b"new content")
        session = MagicMock()
        session.get.return_value = mock_response_cm(resp)

        success, message, filename = await service.download_file(
            session, "https://example.com/report.pdf", str(tmp_path)
        )

        assert success is True
        assert filename == "report_1.pdf"
        assert (tmp_path / "report_1.pdf").read_bytes() == b"new content"
        # Original still untouched
        assert (tmp_path / "report.pdf").read_bytes() == b"existing"


# ---------------------------------------------------------------------------
# download_batch
# ---------------------------------------------------------------------------


class TestDownloadBatch:
    async def test_yields_progress_and_complete(self, tmp_path):
        service = DownloadService()
        docs = [make_doc("https://example.com/a.pdf"), make_doc("https://example.com/b.pdf")]

        with patch.object(service, "download_file", new=AsyncMock(
            return_value=(True, "OK", "file.pdf")
        )):
            events = []
            async for update in service.download_batch(docs, str(tmp_path)):
                events.append(update)

        downloading = [e for e in events if e.status == "downloading"]
        complete = [e for e in events if e.status == "complete"]

        assert len(downloading) == 2
        assert len(complete) == 1
        assert complete[0].files_completed == 2
        assert complete[0].files_failed == 0

    async def test_tracks_failures(self, tmp_path):
        service = DownloadService()
        docs = [
            make_doc("https://example.com/good.pdf"),
            make_doc("https://example.com/bad.pdf"),
        ]

        call_count = 0

        async def mock_download(session, url, path, filename=None):
            nonlocal call_count
            call_count += 1
            if "bad" in url:
                return (False, "HTTP 404", None)
            return (True, "OK", "good.pdf")

        with patch.object(service, "download_file", side_effect=mock_download):
            events = []
            async for update in service.download_batch(docs, str(tmp_path)):
                events.append(update)

        complete = [e for e in events if e.status == "complete"][0]
        assert complete.files_completed == 1
        assert complete.files_failed == 1

    async def test_invalid_directory(self):
        service = DownloadService()
        docs = [make_doc("https://example.com/file.pdf")]

        events = []
        async for update in service.download_batch(docs, "/nonexistent/impossible/path"):
            events.append(update)

        assert len(events) == 1
        assert events[0].status == "error"
        assert "directory" in events[0].message.lower() or "Cannot" in events[0].message
