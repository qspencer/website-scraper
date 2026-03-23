"""Tests for the settings API and service."""

import pytest
from fastapi.testclient import TestClient
from app.services.settings_service import runtime_settings


@pytest.fixture(autouse=True)
def restore_settings():
    """Save and restore all settings so tests don't pollute the real database."""
    original = runtime_settings.get_all().copy()
    yield
    runtime_settings.update(original)


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


class TestAPIKeyProtection:
    """Tests for API key masking and empty-string protection."""

    def test_api_key_masked_in_get(self, client):
        """API key should be masked in GET response."""
        # Save a real key first
        client.put("/api/settings", json={"ai_api_key": "sk-test-secret-key"})

        response = client.get("/api/settings")
        data = response.json()
        assert data["ai_api_key"] == "********"

    def test_empty_api_key_not_masked(self, client):
        """Empty API key should not be masked."""
        # Reset to clear the key
        client.post("/api/settings/reset")

        response = client.get("/api/settings")
        data = response.json()
        assert data["ai_api_key"] == ""

    def test_empty_string_does_not_overwrite_saved_key(self, client):
        """Sending empty string for ai_api_key should not overwrite a saved key."""
        from app.services.settings_service import runtime_settings

        # Save a real key
        client.put("/api/settings", json={"ai_api_key": "sk-real-key"})
        assert runtime_settings.ai_api_key == "sk-real-key"

        # Now send empty string (simulates browser clearing password field)
        client.put("/api/settings", json={"ai_api_key": ""})
        assert runtime_settings.ai_api_key == "sk-real-key"

    def test_masked_placeholder_does_not_overwrite_key(self, client):
        """Sending '********' should not overwrite the saved key."""
        from app.services.settings_service import runtime_settings

        # Save a real key
        client.put("/api/settings", json={"ai_api_key": "sk-real-key"})

        # Send the masked placeholder back (simulates form re-submit)
        client.put("/api/settings", json={"ai_api_key": "********"})
        assert runtime_settings.ai_api_key == "sk-real-key"

    def test_new_key_replaces_old_key(self, client):
        """Sending a new non-empty key should replace the old one."""
        from app.services.settings_service import runtime_settings

        client.put("/api/settings", json={"ai_api_key": "sk-old-key"})
        client.put("/api/settings", json={"ai_api_key": "sk-new-key"})
        assert runtime_settings.ai_api_key == "sk-new-key"

    def test_empty_url_does_not_overwrite_saved_url(self, client):
        """Empty string for ai_api_url should not overwrite a saved value."""
        from app.services.settings_service import runtime_settings

        client.put("/api/settings", json={"ai_api_url": "https://api.openai.com/v1/chat/completions"})
        client.put("/api/settings", json={"ai_api_url": ""})
        assert runtime_settings.ai_api_url == "https://api.openai.com/v1/chat/completions"
