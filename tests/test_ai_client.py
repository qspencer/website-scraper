"""Unit tests for app.services.ai_client — the shared provider-aware chat client.

Focus: the call_chat retry/backoff/Retry-After loop (R-TEST-1), which is the
resilience layer every AI feature (summarization, categorization) depends on and
was previously untested. call_chat_once is mocked so no network is touched.
"""

from unittest.mock import patch, AsyncMock

import pytest

from app.services import ai_client


def _configured_settings():
    """A MagicMock standing in for runtime_settings with AI configured."""
    from unittest.mock import MagicMock
    rs = MagicMock()
    rs.ai_api_url = "https://api.openai.com/v1/chat/completions"
    rs.ai_api_key = "sk-test"
    rs.ai_model = "gpt-test"
    return rs


# --- Provider detection / request shaping (cheap pure functions) -----------


class TestProviderDetection:
    def test_anthropic(self):
        assert ai_client.detect_provider("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_openai(self):
        assert ai_client.detect_provider("https://api.openai.com/v1/chat/completions") == "openai"

    def test_unknown_defaults_openai(self):
        assert ai_client.detect_provider("https://my-llm.example.net/v1/chat") == "openai"


class TestBuildChatRequest:
    def test_openai_uses_max_completion_tokens(self):
        headers, body = ai_client.build_chat_request(
            "openai", "gpt-test", "sk-x", "user text", "sys text", max_tokens=512)
        assert headers["Authorization"] == "Bearer sk-x"
        assert body["max_completion_tokens"] == 512
        assert "max_tokens" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["content"] == "user text"

    def test_anthropic_uses_max_tokens_and_system_field(self):
        headers, body = ai_client.build_chat_request(
            "anthropic", "claude-x", "sk-ant", "user text", "sys text", max_tokens=512)
        assert headers["x-api-key"] == "sk-ant"
        assert body["max_tokens"] == 512
        assert body["system"] == "sys text"


class TestIsRetryable:
    def test_5xx_and_429_retryable(self):
        for code in (429, 500, 502, 503, 504):
            assert ai_client.is_retryable({"error": f"API returned HTTP {code}: x"}) is True

    def test_timeouts_and_conn_retryable(self):
        assert ai_client.is_retryable({"error": "API call timed out after 60 seconds"}) is True
        assert ai_client.is_retryable({"error": "Connection failed: refused"}) is True

    def test_4xx_not_retryable(self):
        assert ai_client.is_retryable({"error": "API returned HTTP 401: unauthorized"}) is False
        assert ai_client.is_retryable({"error": "Failed to parse response as JSON"}) is False


class TestRedactUrl:
    def test_strips_query_and_fragment(self):
        assert ai_client.redact_url("https://h.example/v1/chat?token=secret#frag") == "https://h.example/v1/chat"

    def test_malformed_returns_empty(self):
        assert ai_client.redact_url("not a url") == ""
        assert ai_client.redact_url("") == ""


# --- The retry loop (the point of R-TEST-1) --------------------------------


class TestCallChatRetryLoop:
    async def test_not_configured_returns_error(self):
        from unittest.mock import MagicMock
        rs = MagicMock()
        rs.ai_api_url = ""
        rs.ai_api_key = ""
        rs.ai_model = ""
        with patch.object(ai_client, "runtime_settings", rs):
            result = await ai_client.call_chat("sys", "user")
        assert "error" in result and "not configured" in result["error"]

    @patch.object(ai_client, "call_chat_once", new_callable=AsyncMock)
    async def test_success_first_try_no_retry(self, mock_once):
        mock_once.return_value = {"raw_text": "hello"}
        with patch.object(ai_client, "runtime_settings", _configured_settings()):
            result = await ai_client.call_chat("sys", "user")
        assert result == {"raw_text": "hello"}
        assert mock_once.call_count == 1

    @patch("app.services.ai_client.asyncio.sleep", new_callable=AsyncMock)
    @patch.object(ai_client, "call_chat_once", new_callable=AsyncMock)
    async def test_non_retryable_error_returns_immediately(self, mock_once, mock_sleep):
        mock_once.return_value = {"error": "API returned HTTP 401: unauthorized"}
        with patch.object(ai_client, "runtime_settings", _configured_settings()):
            result = await ai_client.call_chat("sys", "user")
        assert "401" in result["error"]
        assert mock_once.call_count == 1  # no retry on a 4xx
        mock_sleep.assert_not_called()

    @patch("app.services.ai_client.asyncio.sleep", new_callable=AsyncMock)
    @patch.object(ai_client, "call_chat_once", new_callable=AsyncMock)
    async def test_retryable_then_success(self, mock_once, mock_sleep):
        mock_once.side_effect = [
            {"error": "API returned HTTP 500: server error"},
            {"raw_text": "recovered"},
        ]
        with patch.object(ai_client, "runtime_settings", _configured_settings()):
            result = await ai_client.call_chat("sys", "user", label="doc.pdf")
        assert result == {"raw_text": "recovered"}
        assert mock_once.call_count == 2
        mock_sleep.assert_called_once()  # one backoff before the retry

    @patch("app.services.ai_client.asyncio.sleep", new_callable=AsyncMock)
    @patch.object(ai_client, "call_chat_once", new_callable=AsyncMock)
    async def test_exhausts_all_retries(self, mock_once, mock_sleep):
        mock_once.return_value = {"error": "API returned HTTP 503: unavailable"}
        with patch.object(ai_client, "runtime_settings", _configured_settings()):
            result = await ai_client.call_chat("sys", "user")
        # 1 initial + MAX_RETRIES attempts
        assert mock_once.call_count == ai_client.MAX_RETRIES + 1
        assert mock_sleep.call_count == ai_client.MAX_RETRIES
        assert f"Failed after {ai_client.MAX_RETRIES + 1} attempts" in result["error"]

    @patch("app.services.ai_client.asyncio.sleep", new_callable=AsyncMock)
    @patch.object(ai_client, "call_chat_once", new_callable=AsyncMock)
    async def test_honors_retry_after_when_larger_than_default(self, mock_once, mock_sleep):
        # First error carries a Retry-After larger than the default first backoff (5s).
        mock_once.side_effect = [
            {"error": "API returned HTTP 429 | Retry-After: 30s | rate limited"},
            {"raw_text": "ok"},
        ]
        with patch.object(ai_client, "runtime_settings", _configured_settings()):
            result = await ai_client.call_chat("sys", "user")
        assert result == {"raw_text": "ok"}
        # The sleep delay should have been bumped to the 30s Retry-After value.
        slept = mock_sleep.call_args[0][0]
        assert slept == 30
