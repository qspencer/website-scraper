"""Tests for the AI summarization service and endpoints."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services import ai_summarization_service
from app.services.ai_summarization_service import (
    _build_user_prompt,
    _parse_ai_response,
    _call_ai_api,
    summarize_pending_documents,
    is_running,
    MAX_TEXT_LENGTH,
)
from app.services import mongodb_service
from app.api.routes.downloads import download_sessions
from app.api.routes.scraper import scrape_sessions


@pytest.fixture(autouse=True)
def cleanup():
    ai_summarization_service._running = False
    yield
    ai_summarization_service._running = False
    download_sessions.clear()
    scrape_sessions.clear()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    def test_short_text(self):
        prompt = _build_user_prompt("report.pdf", "Short text content")
        assert "report.pdf" in prompt
        assert "Short text content" in prompt
        assert "[Text truncated...]" not in prompt

    def test_long_text_truncated(self):
        long_text = "x" * (MAX_TEXT_LENGTH + 1000)
        prompt = _build_user_prompt("big.pdf", long_text)
        assert "[Text truncated...]" in prompt
        assert len(prompt) < MAX_TEXT_LENGTH + 500  # prompt overhead


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseAIResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "summary": "A financial report for Q4.",
            "keywords": ["finance", "quarterly", "revenue"],
            "document_type": "report",
        })
        result = _parse_ai_response(raw)
        assert result is not None
        assert result["summary"] == "A financial report for Q4."
        assert result["keywords"] == ["finance", "quarterly", "revenue"]
        assert result["document_type"] == "report"

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"summary": "Test summary", "keywords": ["a"], "document_type": "other"}\n```'
        result = _parse_ai_response(raw)
        assert result is not None
        assert result["summary"] == "Test summary"

    def test_json_with_plain_fences(self):
        raw = '```\n{"summary": "Test", "keywords": [], "document_type": "report"}\n```'
        result = _parse_ai_response(raw)
        assert result is not None
        assert result["summary"] == "Test"

    def test_invalid_json(self):
        result = _parse_ai_response("this is not json at all")
        assert result is None

    def test_missing_summary(self):
        raw = json.dumps({"keywords": ["a"], "document_type": "report"})
        result = _parse_ai_response(raw)
        assert result is None

    def test_empty_summary(self):
        raw = json.dumps({"summary": "", "keywords": [], "document_type": "other"})
        result = _parse_ai_response(raw)
        assert result is None

    def test_keywords_not_list(self):
        raw = json.dumps({
            "summary": "A report",
            "keywords": "finance",
            "document_type": "report",
        })
        result = _parse_ai_response(raw)
        assert result is not None
        assert result["keywords"] == []

    def test_missing_document_type_defaults(self):
        raw = json.dumps({"summary": "A report", "keywords": ["a"]})
        result = _parse_ai_response(raw)
        assert result is not None
        assert result["document_type"] == "other"


# ---------------------------------------------------------------------------
# AI API call
# ---------------------------------------------------------------------------


class TestCallAIApi:
    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_not_configured_returns_none(self, mock_settings):
        mock_settings.ai_api_url = ""
        mock_settings.ai_api_key = ""
        mock_settings.ai_model = ""
        result = await _call_ai_api("test.pdf", "some text")
        assert result is None

    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_successful_api_call(self, mock_settings):
        mock_settings.ai_api_url = "https://api.example.com/v1/messages"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        ai_response = {
            "content": [{"text": json.dumps({
                "summary": "Document about testing",
                "keywords": ["test"],
                "document_type": "report",
            })}]
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=ai_response)

        # session.post() returns a sync context manager-like object
        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_cm

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await _call_ai_api("test.pdf", "some text content")

        assert result is not None
        assert result["summary"] == "Document about testing"
        # Verify the API was called with correct headers
        call_kwargs = mock_session.post.call_args
        assert call_kwargs[1]["headers"]["x-api-key"] == "sk-test"

    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_api_error_status(self, mock_settings):
        mock_settings.ai_api_url = "https://api.example.com/v1/messages"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_cm

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await _call_ai_api("test.pdf", "some text")

        assert result is None

    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_api_network_error(self, mock_settings):
        mock_settings.ai_api_url = "https://api.example.com/v1/messages"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("Network error")

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await _call_ai_api("test.pdf", "some text")

        assert result is None


# ---------------------------------------------------------------------------
# Summarize pending documents
# ---------------------------------------------------------------------------


class TestSummarizePendingDocuments:
    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_skips_when_not_configured(self, mock_settings):
        mock_settings.ai_api_url = ""
        mock_settings.ai_api_key = ""
        mock_settings.ai_model = ""

        stats = await summarize_pending_documents()
        assert stats["processed"] == 0

    @patch("app.services.ai_summarization_service._call_ai_api")
    @patch("app.services.ai_summarization_service.mongodb_service")
    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_processes_pending_docs(self, mock_settings, mock_mongo, mock_call):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        mock_mongo.get_pending_summaries.return_value = [
            {"_id": "abc123", "filename": "report.pdf", "extracted_text": "Financial report text"},
        ]

        mock_call.return_value = {
            "summary": "A financial report",
            "keywords": ["finance"],
            "document_type": "report",
        }

        stats = await summarize_pending_documents()

        assert stats["processed"] == 1
        assert stats["succeeded"] == 1
        assert stats["failed"] == 0
        mock_mongo.update_summary.assert_called_once()

    @patch("app.services.ai_summarization_service._call_ai_api")
    @patch("app.services.ai_summarization_service.mongodb_service")
    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_handles_api_failure(self, mock_settings, mock_mongo, mock_call):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        mock_mongo.get_pending_summaries.return_value = [
            {"_id": "abc123", "filename": "report.pdf", "extracted_text": "Some text"},
        ]

        mock_call.return_value = None  # API failed

        stats = await summarize_pending_documents()

        assert stats["processed"] == 1
        assert stats["succeeded"] == 0
        assert stats["failed"] == 1
        mock_mongo.mark_summary_failed.assert_called_once_with("abc123")

    @patch("app.services.ai_summarization_service._call_ai_api")
    @patch("app.services.ai_summarization_service.mongodb_service")
    @patch("app.services.ai_summarization_service.runtime_settings")
    async def test_skips_empty_text(self, mock_settings, mock_mongo, mock_call):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        mock_mongo.get_pending_summaries.return_value = [
            {"_id": "abc123", "filename": "empty.pdf", "extracted_text": "  "},
        ]

        stats = await summarize_pending_documents()

        assert stats["skipped"] == 1
        assert stats["processed"] == 0
        mock_call.assert_not_called()
        mock_mongo.mark_summary_failed.assert_called_once()

    async def test_prevents_concurrent_runs(self):
        ai_summarization_service._running = True
        stats = await summarize_pending_documents()
        assert stats["processed"] == 0

    def test_is_running(self):
        assert is_running() is False
        ai_summarization_service._running = True
        assert is_running() is True


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestSummarizationEndpoints:
    @patch.object(mongodb_service, "get_summary_stats")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_summarization_status(self, mock_settings, mock_stats, client):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"
        mock_stats.return_value = {"pending": 5, "complete": 10, "failed": 2, "skipped": 0}

        resp = client.get("/api/download/mongodb/summarization/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ai_configured"] is True
        assert data["stats"]["pending"] == 5
        assert data["stats"]["complete"] == 10

    @patch("app.api.routes.downloads.runtime_settings")
    def test_summarization_status_not_configured(self, mock_settings, client):
        mock_settings.ai_api_url = ""
        mock_settings.ai_api_key = ""
        mock_settings.ai_model = ""

        resp = client.get("/api/download/mongodb/summarization/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ai_configured"] is False

    def test_start_summarization_not_configured(self, client):
        # runtime_settings has empty AI config by default
        resp = client.post("/api/download/mongodb/summarization/start")
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]

    def test_retry_summarization_not_configured(self, client):
        resp = client.post("/api/download/mongodb/summarization/retry")
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]

    @patch("app.services.ai_summarization_service.is_running", return_value=True)
    @patch("app.api.routes.downloads.runtime_settings")
    def test_start_summarization_already_running(self, mock_settings, mock_running, client):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        resp = client.post("/api/download/mongodb/summarization/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is False
        assert "already running" in data["message"]

    @patch("app.api.routes.downloads.asyncio")
    @patch("app.services.ai_summarization_service.is_running", return_value=False)
    @patch("app.api.routes.downloads.runtime_settings")
    def test_start_summarization_success(self, mock_settings, mock_running, mock_asyncio, client):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        resp = client.post("/api/download/mongodb/summarization/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is True
        mock_asyncio.create_task.assert_called_once()

    @patch("app.api.routes.downloads.asyncio")
    @patch.object(mongodb_service, "reset_failed_summaries", return_value=3)
    @patch("app.api.routes.downloads.runtime_settings")
    def test_retry_failed_summaries(self, mock_settings, mock_reset, mock_asyncio, client):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        resp = client.post("/api/download/mongodb/summarization/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reset_count"] == 3
        assert data["started"] is True
        mock_asyncio.create_task.assert_called_once()

    @patch.object(mongodb_service, "reset_failed_summaries", return_value=0)
    @patch("app.api.routes.downloads.runtime_settings")
    def test_retry_no_failed(self, mock_settings, mock_reset, client):
        mock_settings.ai_api_url = "https://api.example.com"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "test-model"

        resp = client.post("/api/download/mongodb/summarization/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reset_count"] == 0
        assert data["started"] is False
