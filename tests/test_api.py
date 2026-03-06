import pytest
import time
from fastapi.testclient import TestClient

from app.api.routes.scraper import scrape_sessions
from app.api.routes.downloads import download_sessions
from app.services.crawler_service import CrawlState
from app.schemas.scrape import ScrapeResult
from app.schemas.document import DocumentInfo


class TestHealthEndpoint:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "app" in data


class TestIndexPage:
    def test_index_returns_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_index_contains_form(self, client):
        response = client.get("/")
        assert "scrapeForm" in response.text
        assert "Start Scanning" in response.text


class TestScrapeStartEndpoint:
    def test_start_scrape_success(self, client):
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["status"] == "started"

    def test_start_scrape_adds_https(self, client):
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "example.com",  # No https://
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        assert response.status_code == 200

    def test_start_scrape_invalid_filter(self, client):
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "invalid",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        assert response.status_code == 422  # Validation error

    def test_start_scrape_invalid_depth(self, client):
        from app.services.settings_service import runtime_settings
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": runtime_settings.max_crawl_depth + 1,
            },
        )
        assert response.status_code == 422

    def test_start_scrape_empty_url(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any("enter a website address" in e["msg"].lower() for e in detail)

    def test_start_scrape_whitespace_url(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "   ", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422

    def test_start_scrape_no_domain(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "https://", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any("valid website address" in e["msg"].lower() for e in detail)

    def test_start_scrape_no_tld(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "mysite", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any("mysite.com" in e["msg"] for e in detail)

    def test_start_scrape_url_with_spaces(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "https://example .com/page", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any("spaces" in e["msg"].lower() for e in detail)

    def test_start_scrape_single_char_tld(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "example.x", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 422

    def test_start_scrape_localhost_allowed(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "http://localhost:8080", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 200

    def test_start_scrape_valid_url_with_path(self, client):
        response = client.post(
            "/api/scrape/start",
            json={"url": "https://example.com/docs/page.html", "document_type_filter": "common", "crawl_option": "single", "max_depth": 2},
        )
        assert response.status_code == 200


class TestScrapeResultsEndpoint:
    def test_results_not_found(self, client):
        response = client.get("/api/scrape/results/nonexistent-id")
        assert response.status_code == 404

    def test_results_not_started(self, client):
        # First create a session
        start_response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start_response.json()["session_id"]

        # Results shouldn't be available yet (scan not run)
        response = client.get(f"/api/scrape/results/{session_id}")
        assert response.status_code == 400


class TestScrapeCancelEndpoint:
    def test_cancel_not_found(self, client):
        response = client.delete("/api/scrape/cancel/nonexistent-id")
        assert response.status_code == 404

    def test_cancel_success(self, client):
        # Create session first
        start_response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start_response.json()["session_id"]

        response = client.delete(f"/api/scrape/cancel/{session_id}")
        assert response.status_code == 200


class TestDownloadValidatePath:
    def test_validate_valid_path(self, client):
        response = client.post(
            "/api/download/validate-path",
            json={"path": "/tmp"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    def test_validate_empty_path(self, client):
        response = client.post(
            "/api/download/validate-path",
            json={"path": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False


class TestDownloadStartEndpoint:
    def test_start_invalid_session(self, client):
        response = client.post(
            "/api/download/start",
            json={
                "session_id": "nonexistent",
                "document_urls": ["https://example.com/file.pdf"],
                "download_path": "/tmp",
            },
        )
        assert response.status_code == 404

    def test_start_invalid_path(self, client):
        # Create a scrape session first
        start_response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start_response.json()["session_id"]

        response = client.post(
            "/api/download/start",
            json={
                "session_id": session_id,
                "document_urls": ["https://example.com/file.pdf"],
                "download_path": "",
            },
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Additional scrape endpoint tests
# ---------------------------------------------------------------------------


def _make_doc(url="https://example.com/file.pdf"):
    return DocumentInfo(
        url=url,
        filename=url.rsplit("/", 1)[-1],
        extension=".pdf",
        source_page="https://example.com",
        depth=0,
    )


class TestResultsPage:
    def test_results_page_loads(self, client):
        response = client.get("/results/some-session-id")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


class TestScrapeStartScanAllPages:
    def test_accepts_scan_all_pages(self, client):
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "follow",
                "max_depth": 2,
                "scan_all_pages": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

    def test_defaults_scan_all_pages_false(self, client):
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        assert response.status_code == 200


class TestScrapeContinueEndpoint:
    def test_continue_not_found(self, client):
        response = client.post("/api/scrape/continue/nonexistent-id")
        assert response.status_code == 404

    def test_continue_no_state(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        response = client.post(f"/api/scrape/continue/{session_id}")
        assert response.status_code == 400
        assert "No continuation state" in response.json()["detail"]

    def test_continue_no_pages_remaining(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "follow",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        # Manually set crawl_state with empty pending queue
        state = CrawlState()
        state.pending_queue = []
        scrape_sessions[session_id]["crawl_state"] = state

        response = client.post(f"/api/scrape/continue/{session_id}")
        assert response.status_code == 400
        assert "No more pages" in response.json()["detail"]


class TestScrapeSummaryEndpoint:
    def test_summary_not_found(self, client):
        response = client.get("/api/scrape/summary/nonexistent-id")
        assert response.status_code == 404

    def test_summary_with_results(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        # Simulate completed scan
        scrape_sessions[session_id]["result"] = ScrapeResult(
            success=True,
            documents=[_make_doc()],
            pages_scanned=3,
        )
        scrape_sessions[session_id]["start_time"] = time.time() - 5
        scrape_sessions[session_id]["end_time"] = time.time()

        response = client.get(f"/api/scrape/summary/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["pages_scanned"] == 3
        assert data["documents_found"] == 1
        assert data["duration_seconds"] is not None
        assert data["duration_seconds"] > 0
        assert data["scan_all_pages"] is False
        assert data["scan_error_count"] == 0
        assert data["document_error_count"] == 0

    def test_summary_scan_all_pages(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "follow",
                "max_depth": 2,
                "scan_all_pages": True,
            },
        )
        session_id = start.json()["session_id"]

        response = client.get(f"/api/scrape/summary/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["scan_all_pages"] is True

    def test_summary_before_results(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        response = client.get(f"/api/scrape/summary/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["pages_scanned"] == 0
        assert data["documents_found"] == 0


class TestScrapeSessionCleanup:
    def test_cleanup_existing(self, client):
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        response = client.delete(f"/api/scrape/session/{session_id}")
        assert response.status_code == 200

        # Session should be gone
        response = client.get(f"/api/scrape/results/{session_id}")
        assert response.status_code == 404

    def test_cleanup_nonexistent(self, client):
        response = client.delete("/api/scrape/session/nonexistent-id")
        assert response.status_code == 200  # Idempotent


# ---------------------------------------------------------------------------
# Additional download endpoint tests
# ---------------------------------------------------------------------------


class TestDownloadCancelEndpoint:
    def test_cancel_not_found(self, client):
        response = client.delete("/api/download/cancel/nonexistent-id")
        assert response.status_code == 404

    def test_cancel_success(self, client):
        # Manually create a download session
        download_sessions["test-dl-id"] = {
            "documents": [],
            "download_path": "/tmp",
            "status": "downloading",
            "cancelled": False,
        }

        response = client.delete("/api/download/cancel/test-dl-id")
        assert response.status_code == 200
        assert download_sessions["test-dl-id"]["cancelled"] is True

        # Clean up
        del download_sessions["test-dl-id"]


class TestDownloadSessionCleanup:
    def test_cleanup_existing(self, client):
        download_sessions["test-dl-cleanup"] = {
            "documents": [],
            "download_path": "/tmp",
            "status": "complete",
            "cancelled": False,
        }

        response = client.delete("/api/download/session/test-dl-cleanup")
        assert response.status_code == 200
        assert "test-dl-cleanup" not in download_sessions

    def test_cleanup_nonexistent(self, client):
        response = client.delete("/api/download/session/nonexistent-id")
        assert response.status_code == 200


class TestDownloadStartWithResults:
    def test_start_no_results(self, client):
        """Download start fails when scrape has no results."""
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        response = client.post(
            "/api/download/start",
            json={
                "session_id": session_id,
                "document_urls": ["https://example.com/file.pdf"],
                "download_path": "/tmp",
            },
        )
        assert response.status_code == 400

    def test_start_no_matching_docs(self, client):
        """Download start fails when no selected URLs match results."""
        start = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 2,
            },
        )
        session_id = start.json()["session_id"]

        # Set up results with a document
        scrape_sessions[session_id]["result"] = ScrapeResult(
            success=True,
            documents=[_make_doc("https://example.com/actual.pdf")],
            pages_scanned=1,
        )

        response = client.post(
            "/api/download/start",
            json={
                "session_id": session_id,
                "document_urls": ["https://example.com/different.pdf"],
                "download_path": "/tmp",
            },
        )
        assert response.status_code == 400
