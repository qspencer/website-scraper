import pytest
from fastapi.testclient import TestClient


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
        response = client.post(
            "/api/scrape/start",
            json={
                "url": "https://example.com",
                "document_type_filter": "common",
                "crawl_option": "single",
                "max_depth": 10,  # Exceeds max
            },
        )
        assert response.status_code == 422


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
