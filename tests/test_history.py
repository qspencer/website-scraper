"""Tests for scan history feature."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.services.history_service import save_scan, get_history, clear_history
from app.api.routes.scraper import _save_scan_history
from app.schemas.scrape import ScrapeResult
from app.schemas.document import DocumentInfo
from app.core.constants import CrawlDepthOption, DocumentTypeFilter
from app.core import database as db


@pytest.fixture(autouse=True)
def isolate_history():
    """Clear history before each test (tests run against a temp database)."""
    clear_history()
    yield


def _make_scan_data(**overrides):
    """Create a minimal scan_data dict with defaults."""
    data = {
        "url": "https://example.com",
        "crawl_option": "single",
        "max_depth": 1,
        "scan_mode": "single page",
        "document_filter": "common",
        "pages_scanned": 5,
        "documents_found": 3,
        "scan_error_count": 0,
        "document_error_count": 0,
        "duration_seconds": 12.5,
        "total_size_bytes": 1024000,
        "largest_file_name": "big.pdf",
        "largest_file_size": 500000,
        "smallest_file_name": "small.pdf",
        "smallest_file_size": 1000,
    }
    data.update(overrides)
    return data


class TestHistoryService:
    """Tests for history_service functions."""

    def test_save_and_retrieve_scan(self):
        save_scan(_make_scan_data(url="https://example.com/page1"))
        history = get_history()
        assert len(history) == 1
        assert history[0]["url"] == "https://example.com/page1"
        assert history[0]["pages_scanned"] == 5
        assert history[0]["documents_found"] == 3

    def test_get_history_returns_newest_first(self):
        save_scan(_make_scan_data(url="https://first.com"))
        save_scan(_make_scan_data(url="https://second.com"))
        save_scan(_make_scan_data(url="https://third.com"))

        history = get_history()
        assert len(history) == 3
        assert history[0]["url"] == "https://third.com"
        assert history[1]["url"] == "https://second.com"
        assert history[2]["url"] == "https://first.com"

    def test_get_history_respects_limit_param(self):
        for i in range(5):
            save_scan(_make_scan_data(url=f"https://site{i}.com"))

        history = get_history(limit=2)
        assert len(history) == 2

    def test_clear_history(self):
        save_scan(_make_scan_data())
        save_scan(_make_scan_data())
        assert len(get_history()) == 2

        clear_history()
        assert len(get_history()) == 0

    def test_pruning_removes_old_entries(self):
        """Saving beyond the limit should prune oldest entries."""
        with patch("app.services.history_service.runtime_settings") as mock_settings:
            mock_settings.scan_history_limit = 3

            for i in range(5):
                save_scan(_make_scan_data(url=f"https://site{i}.com"))

            # Should only keep the 3 most recent
            history = get_history(limit=100)
            assert len(history) == 3
            urls = [h["url"] for h in history]
            assert "https://site4.com" in urls
            assert "https://site3.com" in urls
            assert "https://site2.com" in urls
            assert "https://site0.com" not in urls
            assert "https://site1.com" not in urls

    def test_save_scan_stores_all_fields(self):
        scan_data = _make_scan_data(
            url="https://full-test.com",
            crawl_option="follow",
            max_depth=3,
            scan_mode="continuous",
            document_filter="all",
            pages_scanned=42,
            documents_found=10,
            scan_error_count=2,
            document_error_count=1,
            duration_seconds=99.9,
            total_size_bytes=5000000,
            largest_file_name="huge.pdf",
            largest_file_size=3000000,
            smallest_file_name="tiny.txt",
            smallest_file_size=500,
        )
        save_scan(scan_data)

        history = get_history()
        entry = history[0]
        assert entry["url"] == "https://full-test.com"
        assert entry["crawl_option"] == "follow"
        assert entry["max_depth"] == 3
        assert entry["scan_mode"] == "continuous"
        assert entry["document_filter"] == "all"
        assert entry["pages_scanned"] == 42
        assert entry["documents_found"] == 10
        assert entry["scan_error_count"] == 2
        assert entry["document_error_count"] == 1
        assert entry["duration_seconds"] == pytest.approx(99.9)
        assert entry["total_size_bytes"] == 5000000
        assert entry["largest_file_name"] == "huge.pdf"
        assert entry["largest_file_size"] == 3000000
        assert entry["smallest_file_name"] == "tiny.txt"
        assert entry["smallest_file_size"] == 500
        assert entry["completed_at"] is not None

    def test_save_scan_with_nullable_fields(self):
        scan_data = _make_scan_data(
            duration_seconds=None,
            total_size_bytes=None,
            largest_file_name=None,
            largest_file_size=None,
            smallest_file_name=None,
            smallest_file_size=None,
        )
        save_scan(scan_data)

        entry = get_history()[0]
        assert entry["duration_seconds"] is None
        assert entry["total_size_bytes"] is None
        assert entry["largest_file_name"] is None


class TestHistoryAPI:
    """Tests for history API endpoints."""

    def test_get_history_empty(self, client):
        response = client.get("/api/history")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_history_with_data(self, client):
        save_scan(_make_scan_data(url="https://api-test.com"))

        response = client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["url"] == "https://api-test.com"

    def test_delete_history(self, client):
        save_scan(_make_scan_data())
        save_scan(_make_scan_data())

        response = client.delete("/api/history")
        assert response.status_code == 200
        assert response.json()["message"] == "History cleared"

        # Verify empty
        response = client.get("/api/history")
        assert response.json() == []

    def test_history_page_loads(self, client):
        response = client.get("/history")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Scan History" in response.text


class TestSaveScanHistoryHelper:
    """Tests for _save_scan_history() in scraper routes."""

    def _make_session(self, *, crawl_option="single", scan_all_pages=False,
                      docs=None, errors=None, start_time=100.0, end_time=112.5):
        """Build a session dict matching the structure used in scraper routes."""
        if docs is None:
            docs = [
                DocumentInfo(
                    url="https://example.com/a.pdf", filename="a.pdf",
                    extension=".pdf", source_page="https://example.com",
                    file_size_bytes=5000, is_accessible=True,
                ),
                DocumentInfo(
                    url="https://example.com/b.pdf", filename="b.pdf",
                    extension=".pdf", source_page="https://example.com",
                    file_size_bytes=1000, is_accessible=True,
                ),
            ]

        request = MagicMock()
        request.url = "https://example.com"
        request.crawl_option.value = crawl_option
        request.max_depth = 2
        request.scan_all_pages = scan_all_pages
        request.document_type_filter.value = "common"

        result = ScrapeResult(
            success=True,
            documents=docs,
            pages_scanned=10,
            errors=errors or [],
        )

        return {
            "request": request,
            "result": result,
            "start_time": start_time,
            "end_time": end_time,
        }

    def test_saves_single_page_mode(self):
        session = self._make_session(crawl_option="single")
        _save_scan_history(session)

        history = get_history()
        assert len(history) == 1
        assert history[0]["scan_mode"] == "single page"

    def test_saves_batch_mode(self):
        session = self._make_session(crawl_option="follow", scan_all_pages=False)
        _save_scan_history(session)

        history = get_history()
        assert history[0]["scan_mode"] == "batch"

    def test_saves_continuous_mode(self):
        session = self._make_session(crawl_option="follow", scan_all_pages=True)
        _save_scan_history(session)

        history = get_history()
        assert history[0]["scan_mode"] == "continuous"

    def test_computes_duration(self):
        session = self._make_session(start_time=100.0, end_time=115.5)
        _save_scan_history(session)

        history = get_history()
        assert history[0]["duration_seconds"] == pytest.approx(15.5)

    def test_computes_size_stats(self):
        docs = [
            DocumentInfo(
                url="https://example.com/big.pdf", filename="big.pdf",
                extension=".pdf", source_page="https://example.com",
                file_size_bytes=50000, is_accessible=True,
            ),
            DocumentInfo(
                url="https://example.com/med.pdf", filename="med.pdf",
                extension=".pdf", source_page="https://example.com",
                file_size_bytes=10000, is_accessible=True,
            ),
            DocumentInfo(
                url="https://example.com/small.pdf", filename="small.pdf",
                extension=".pdf", source_page="https://example.com",
                file_size_bytes=500, is_accessible=True,
            ),
        ]
        session = self._make_session(docs=docs)
        _save_scan_history(session)

        entry = get_history()[0]
        assert entry["total_size_bytes"] == 60500
        assert entry["largest_file_name"] == "big.pdf"
        assert entry["largest_file_size"] == 50000
        assert entry["smallest_file_name"] == "small.pdf"
        assert entry["smallest_file_size"] == 500

    def test_handles_no_sized_documents(self):
        docs = [
            DocumentInfo(
                url="https://example.com/a.pdf", filename="a.pdf",
                extension=".pdf", source_page="https://example.com",
                file_size_bytes=None, is_accessible=True,
            ),
        ]
        session = self._make_session(docs=docs)
        _save_scan_history(session)

        entry = get_history()[0]
        assert entry["total_size_bytes"] is None
        assert entry["largest_file_name"] is None
        assert entry["smallest_file_name"] is None

    def test_counts_errors(self):
        docs = [
            DocumentInfo(
                url="https://example.com/good.pdf", filename="good.pdf",
                extension=".pdf", source_page="https://example.com",
                is_accessible=True,
            ),
            DocumentInfo(
                url="https://example.com/bad.pdf", filename="bad.pdf",
                extension=".pdf", source_page="https://example.com",
                is_accessible=False, error_message="404",
            ),
            DocumentInfo(
                url="https://example.com/bad2.pdf", filename="bad2.pdf",
                extension=".pdf", source_page="https://example.com",
                is_accessible=False, error_message="403",
            ),
        ]
        session = self._make_session(
            docs=docs,
            errors=["Failed to fetch page1", "Timeout on page2"],
        )
        _save_scan_history(session)

        entry = get_history()[0]
        assert entry["scan_error_count"] == 2
        assert entry["document_error_count"] == 2

    def test_skips_when_no_result(self):
        session = {
            "request": MagicMock(),
            "result": None,
            "start_time": 100.0,
            "end_time": 110.0,
        }
        _save_scan_history(session)

        assert len(get_history()) == 0

    def test_handles_exception_gracefully(self):
        """_save_scan_history should log errors but not raise."""
        session = self._make_session()
        with patch("app.api.routes.scraper.save_scan", side_effect=Exception("db error")):
            # Should not raise
            _save_scan_history(session)

        # Nothing saved since save_scan was mocked to fail
        assert len(get_history()) == 0

    def test_handles_missing_times(self):
        session = self._make_session()
        session["start_time"] = None
        session["end_time"] = None
        _save_scan_history(session)

        entry = get_history()[0]
        assert entry["duration_seconds"] is None
