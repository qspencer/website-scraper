"""Tests for SSE progress streaming in scraper routes."""

import json
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.api.routes.scraper import scrape_sessions
from app.schemas.scrape import ScrapeRequest, ScrapeProgress, ScrapeResult
from app.schemas.document import DocumentInfo
from app.services.crawler_service import CrawlState
from app.core.constants import DocumentTypeFilter, CrawlDepthOption


def _make_doc(url="https://example.com/file.pdf"):
    return DocumentInfo(
        url=url,
        filename=url.rsplit("/", 1)[-1],
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
    )


def _create_session(session_id="test-session", **overrides):
    """Create a scrape session entry and return the session_id."""
    request = ScrapeRequest(
        url="https://example.com",
        document_type_filter=DocumentTypeFilter.COMMON,
        crawl_option=CrawlDepthOption.SINGLE_PAGE,
        max_depth=2,
    )
    scrape_sessions[session_id] = {
        "request": request,
        "status": "pending",
        "result": None,
        "cancelled": False,
        "crawl_state": None,
        "start_time": 100.0,
        "end_time": None,
    }
    scrape_sessions[session_id].update(overrides)
    return session_id


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    scrape_sessions.clear()


# ---------------------------------------------------------------------------
# scrape_progress SSE endpoint
# ---------------------------------------------------------------------------


class TestScrapeProgressSSE:
    async def test_progress_then_complete(self, client):
        """Normal flow: progress events followed by complete."""
        session_id = _create_session()

        progress = ScrapeProgress(
            status="scanning",
            current_page="Batch 1",
            pages_scanned=1,
            documents_found=2,
            message="Scanning...",
        )
        result = ScrapeResult(
            success=True,
            documents=[_make_doc()],
            pages_scanned=5,
        )

        async def mock_crawl(**kwargs):
            yield progress
            yield result

        with patch("app.api.routes.scraper.crawler_service") as mock_cs:
            mock_cs.crawl = mock_crawl
            response = client.get(f"/api/scrape/progress/{session_id}")

        assert response.status_code == 200
        # Parse SSE events from response body
        body = response.text
        assert "event: progress" in body
        assert "event: complete" in body
        # Session should be marked complete
        assert scrape_sessions[session_id]["status"] == "complete"
        assert scrape_sessions[session_id]["result"] is not None

    async def test_cancellation_saves_partial_results(self, client):
        """When cancelled, partial results are saved and cancelled event is sent."""
        session_id = _create_session()

        async def mock_crawl(**kwargs):
            state = kwargs.get("state")
            state.all_documents.append(_make_doc())
            state.pages_scanned = 3
            # First yield triggers cancellation check
            yield ScrapeProgress(
                status="scanning",
                pages_scanned=3,
                documents_found=1,
                message="Scanning...",
            )
            # Simulate more progress that shouldn't be reached
            yield ScrapeProgress(
                status="scanning",
                pages_scanned=10,
                documents_found=5,
                message="More scanning...",
            )

        def cancel_on_progress(**kwargs):
            """Set cancelled after crawl starts."""
            async def gen(**kw):
                state = kw.get("state")
                state.all_documents.append(_make_doc())
                state.pages_scanned = 3
                yield ScrapeProgress(status="scanning", pages_scanned=1, message="a")
                # After first yield, session should be cancelled
                yield ScrapeProgress(status="scanning", pages_scanned=2, message="b")
            return gen(**kwargs)

        with patch("app.api.routes.scraper.crawler_service") as mock_cs:
            # We need the cancellation to happen after the first yield
            # The simplest way: set cancelled before the request
            scrape_sessions[session_id]["cancelled"] = True

            mock_cs.crawl = mock_crawl
            response = client.get(f"/api/scrape/progress/{session_id}")

        body = response.text
        assert "event: cancelled" in body
        # Partial results should be saved
        session = scrape_sessions[session_id]
        assert session["result"] is not None
        assert len(session["result"].documents) == 1
        assert session["status"] == "complete"

    async def test_error_during_crawl(self, client):
        """Exception in crawl yields error event."""
        session_id = _create_session()

        async def mock_crawl(**kwargs):
            raise RuntimeError("Unexpected failure")
            yield  # make it a generator  # noqa: E501

        with patch("app.api.routes.scraper.crawler_service") as mock_cs:
            mock_cs.crawl = mock_crawl
            response = client.get(f"/api/scrape/progress/{session_id}")

        body = response.text
        assert "event: error" in body
        assert "Unexpected failure" in body
        assert scrape_sessions[session_id]["status"] == "error"

    async def test_not_found_session(self, client):
        """Unknown session returns 404."""
        response = client.get("/api/scrape/progress/nonexistent-id")
        assert response.status_code == 404

    async def test_continuation_uses_existing_state(self, client):
        """Continuation should pass existing crawl state."""
        state = CrawlState()
        state.pending_queue = [("https://example.com/page2", 1)]
        state.pages_scanned = 5
        state.all_documents = [_make_doc()]

        session_id = _create_session(crawl_state=state)

        captured_state = {}

        async def mock_crawl(**kwargs):
            captured_state["state"] = kwargs.get("state")
            yield ScrapeResult(
                success=True,
                documents=[_make_doc()],
                pages_scanned=6,
            )

        with patch("app.api.routes.scraper.crawler_service") as mock_cs:
            mock_cs.crawl = mock_crawl
            response = client.get(f"/api/scrape/progress/{session_id}")

        assert response.status_code == 200
        # The same state object should be passed
        assert captured_state["state"] is state

    async def test_stores_crawl_state_for_new_crawl(self, client):
        """New crawl creates and stores a CrawlState."""
        session_id = _create_session()

        async def mock_crawl(**kwargs):
            yield ScrapeResult(
                success=True, documents=[], pages_scanned=1,
            )

        with patch("app.api.routes.scraper.crawler_service") as mock_cs:
            mock_cs.crawl = mock_crawl
            response = client.get(f"/api/scrape/progress/{session_id}")

        assert response.status_code == 200
        assert scrape_sessions[session_id]["crawl_state"] is not None
        assert isinstance(scrape_sessions[session_id]["crawl_state"], CrawlState)


# ---------------------------------------------------------------------------
# calculate-sizes SSE endpoint
# ---------------------------------------------------------------------------


class TestCalculateSizesSSE:
    async def test_progress_and_complete(self, client):
        """Normal flow: progress per URL then complete event."""
        session_id = _create_session()

        async def mock_get_size(session, url):
            return 5000

        with patch("app.api.routes.scraper.scraper_service") as mock_ss:
            mock_ss.get_file_size_via_get = AsyncMock(side_effect=mock_get_size)
            response = client.post(
                f"/api/scrape/calculate-sizes/{session_id}",
                json={"urls": [
                    "https://example.com/a.pdf",
                    "https://example.com/b.pdf",
                ]},
            )

        assert response.status_code == 200
        body = response.text
        # Should have 2 progress events and 1 complete
        assert body.count("event: progress") == 2
        assert "event: complete" in body
        # Complete event should have known_count=2
        # Find the complete event data
        for line in body.split("\n"):
            if line.startswith("data:") and "known_count" in line:
                data = json.loads(line[len("data:"):])
                if "files" in data:  # complete event
                    assert data["known_count"] == 2
                    assert data["total_bytes"] == 10000

    async def test_unknown_sizes(self, client):
        """URLs with unknown sizes should have null size."""
        session_id = _create_session()

        with patch("app.api.routes.scraper.scraper_service") as mock_ss:
            mock_ss.get_file_size_via_get = AsyncMock(return_value=None)
            response = client.post(
                f"/api/scrape/calculate-sizes/{session_id}",
                json={"urls": ["https://example.com/unknown.pdf"]},
            )

        body = response.text
        assert "event: complete" in body
        for line in body.split("\n"):
            if line.startswith("data:") and "unknown_count" in line:
                data = json.loads(line[len("data:"):])
                if "files" in data:
                    assert data["unknown_count"] == 1
                    assert data["known_count"] == 0

    async def test_not_found_session(self, client):
        response = client.post(
            "/api/scrape/calculate-sizes/nonexistent",
            json={"urls": ["https://example.com/a.pdf"]},
        )
        assert response.status_code == 404

    async def test_no_urls_provided(self, client):
        session_id = _create_session()
        response = client.post(
            f"/api/scrape/calculate-sizes/{session_id}",
            json={"urls": []},
        )
        assert response.status_code == 400

    async def test_missing_urls_key(self, client):
        session_id = _create_session()
        response = client.post(
            f"/api/scrape/calculate-sizes/{session_id}",
            json={},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# retry SSE endpoint
# ---------------------------------------------------------------------------


def _make_failed_doc(url="https://example.com/broken.pdf", error="HTTP 404"):
    return DocumentInfo(
        url=url,
        filename=url.rsplit("/", 1)[-1],
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
        is_accessible=False,
        error_message=error,
    )


def _make_accessible_doc(url="https://example.com/good.pdf", size=5000):
    return DocumentInfo(
        url=url,
        filename=url.rsplit("/", 1)[-1],
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
        is_accessible=True,
        file_size_bytes=size,
        file_size_display=f"{size} B",
    )


class TestRetrySSE:
    async def test_retry_fixes_document(self, client):
        """Retrying a failed document that now succeeds."""
        failed_doc = _make_failed_doc("https://example.com/recovered.pdf")
        state = CrawlState()
        state.document_urls.add(failed_doc.url)
        state.document_filenames.add(failed_doc.filename.lower())

        result = ScrapeResult(
            success=True, documents=[failed_doc], pages_scanned=1, errors=[]
        )
        session_id = _create_session(
            result=result, crawl_state=state, status="complete"
        )

        recovered = _make_accessible_doc("https://example.com/recovered.pdf")

        with patch("app.api.routes.scraper.scraper_service") as mock_ss:
            mock_ss.get_file_info = AsyncMock(return_value=recovered)
            response = client.post(f"/api/scrape/retry/{session_id}")

        assert response.status_code == 200
        body = response.text
        assert "event: progress" in body
        assert "event: complete" in body

        for line in body.split("\n"):
            if line.startswith("data:") and "docs_fixed" in line:
                data = json.loads(line[len("data:"):])
                if "remaining_doc_errors" in data:
                    assert data["docs_fixed"] == 1
                    assert data["remaining_doc_errors"] == 0

    async def test_retry_document_still_fails(self, client):
        """Document that still fails on retry."""
        failed_doc = _make_failed_doc()
        state = CrawlState()

        result = ScrapeResult(
            success=True, documents=[failed_doc], pages_scanned=1, errors=[]
        )
        session_id = _create_session(
            result=result, crawl_state=state, status="complete"
        )

        still_failed = _make_failed_doc()

        with patch("app.api.routes.scraper.scraper_service") as mock_ss:
            mock_ss.get_file_info = AsyncMock(return_value=still_failed)
            response = client.post(f"/api/scrape/retry/{session_id}")

        assert response.status_code == 200
        for line in response.text.split("\n"):
            if line.startswith("data:") and "docs_fixed" in line:
                data = json.loads(line[len("data:"):])
                if "remaining_doc_errors" in data:
                    assert data["docs_fixed"] == 0
                    assert data["remaining_doc_errors"] == 1

    async def test_retry_failed_page(self, client):
        """Retrying a failed page that now returns documents."""
        state = CrawlState()
        state.failed_pages = [("https://example.com/page2", 1)]
        state.errors = ["Error scanning https://example.com/page2: Request timed out"]

        result = ScrapeResult(
            success=True, documents=[], pages_scanned=2,
            errors=state.errors.copy()
        )
        session_id = _create_session(
            result=result, crawl_state=state, status="complete"
        )

        new_doc = _make_accessible_doc("https://example.com/found.pdf")

        with patch("app.api.routes.scraper.scraper_service") as mock_ss:
            mock_ss.scan_page = AsyncMock(return_value=([], [new_doc], None))
            response = client.post(f"/api/scrape/retry/{session_id}")

        assert response.status_code == 200
        for line in response.text.split("\n"):
            if line.startswith("data:") and "pages_fixed" in line:
                data = json.loads(line[len("data:"):])
                if "new_docs_found" in data:
                    assert data["pages_fixed"] == 1
                    assert data["new_docs_found"] == 1

    async def test_retry_not_found(self, client):
        response = client.post("/api/scrape/retry/nonexistent")
        assert response.status_code == 404

    async def test_retry_no_result(self, client):
        session_id = _create_session()
        response = client.post(f"/api/scrape/retry/{session_id}")
        assert response.status_code == 400

    async def test_retry_no_errors(self, client):
        """No retryable errors returns 400."""
        good_doc = _make_accessible_doc()
        state = CrawlState()

        result = ScrapeResult(
            success=True, documents=[good_doc], pages_scanned=1, errors=[]
        )
        session_id = _create_session(
            result=result, crawl_state=state, status="complete"
        )
        response = client.post(f"/api/scrape/retry/{session_id}")
        assert response.status_code == 400
