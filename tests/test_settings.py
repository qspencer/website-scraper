"""Tests for the settings API and service."""

import pytest
from fastapi.testclient import TestClient


class TestSettingsAPI:
    """Tests for settings API endpoints."""

    def test_get_settings(self, client):
        """Test getting current settings."""
        response = client.get("/api/settings")
        assert response.status_code == 200

        data = response.json()
        assert "max_pages_per_scan" in data
        assert "request_timeout" in data
        assert "requests_per_second" in data
        assert "default_crawl_depth" in data
        assert "max_crawl_depth" in data

    def test_update_single_setting(self, client):
        """Test updating a single setting."""
        response = client.put(
            "/api/settings",
            json={"max_pages_per_scan": 100}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["max_pages_per_scan"] == 100

    def test_update_multiple_settings(self, client):
        """Test updating multiple settings at once."""
        response = client.put(
            "/api/settings",
            json={
                "max_pages_per_scan": 200,
                "request_timeout": 60,
                "requests_per_second": 5.0
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert data["max_pages_per_scan"] == 200
        assert data["request_timeout"] == 60
        assert data["requests_per_second"] == 5.0

    def test_update_enforces_min_values(self, client):
        """Test that minimum values are enforced."""
        response = client.put(
            "/api/settings",
            json={"max_pages_per_scan": 1}  # Below minimum of 10
        )
        assert response.status_code == 200

        data = response.json()
        assert data["max_pages_per_scan"] >= 10

    def test_update_enforces_max_values(self, client):
        """Test that maximum values are enforced."""
        response = client.put(
            "/api/settings",
            json={"max_pages_per_scan": 99999}  # Above maximum of 10000
        )
        assert response.status_code == 200

        data = response.json()
        assert data["max_pages_per_scan"] <= 10000

    def test_reset_settings(self, client):
        """Test resetting settings to defaults."""
        # First, change a setting
        client.put("/api/settings", json={"max_pages_per_scan": 999})

        # Then reset
        response = client.post("/api/settings/reset")
        assert response.status_code == 200

        # Verify it was reset
        data = response.json()
        # Should be back to default (500)
        assert data["max_pages_per_scan"] == 500


class TestSettingsPage:
    """Tests for settings HTML page."""

    def test_settings_page_loads(self, client):
        """Test that the settings page loads."""
        response = client.get("/settings")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_settings_page_contains_form(self, client):
        """Test that the settings page contains the form elements."""
        response = client.get("/settings")
        content = response.text

        assert "max_pages_per_scan" in content
        assert "request_timeout" in content
        assert "requests_per_second" in content
        assert "Save Settings" in content


class TestSettingsValidation:
    """Tests for settings validation."""

    def test_request_timeout_min(self, client):
        """Test request_timeout minimum value."""
        response = client.put("/api/settings", json={"request_timeout": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["request_timeout"] >= 5

    def test_request_timeout_max(self, client):
        """Test request_timeout maximum value."""
        response = client.put("/api/settings", json={"request_timeout": 999})
        assert response.status_code == 200
        data = response.json()
        assert data["request_timeout"] <= 120

    def test_requests_per_second_min(self, client):
        """Test requests_per_second minimum value."""
        response = client.put("/api/settings", json={"requests_per_second": 0.1})
        assert response.status_code == 200
        data = response.json()
        assert data["requests_per_second"] >= 0.5

    def test_requests_per_second_max(self, client):
        """Test requests_per_second maximum value."""
        response = client.put("/api/settings", json={"requests_per_second": 100})
        assert response.status_code == 200
        data = response.json()
        assert data["requests_per_second"] <= 10.0

    def test_crawl_depth_min(self, client):
        """Test crawl depth minimum value."""
        response = client.put("/api/settings", json={"default_crawl_depth": 0})
        assert response.status_code == 200
        data = response.json()
        assert data["default_crawl_depth"] >= 1

    def test_crawl_depth_max(self, client):
        """Test crawl depth maximum value."""
        response = client.put("/api/settings", json={"max_crawl_depth": 100})
        assert response.status_code == 200
        data = response.json()
        assert data["max_crawl_depth"] <= 10
