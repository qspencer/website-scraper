"""Generic, provider-aware client for the configured AI chat endpoint.

Extracted from ai_summarization_service so that other features (categorization,
future analyses) can share the same HTTP / retry / provider-detection plumbing
without depending on summarization-specific data shapes.

This module knows about:
  * provider detection from the URL (Anthropic vs OpenAI-compatible)
  * the two chat-request body shapes
  * the two response-shape extractors
  * timeouts, retry policy, and retryable-status classification
  * how to redact a URL for user-facing error strings

It does NOT know about:
  * what the prompt asks for (summary? categories? something else?)
  * how to parse the model's reply (JSON schema validation is the caller's job)

The two public entry points are ``call_chat_once`` (one HTTP call) and
``call_chat`` (one call plus retries, configured from runtime_settings).
"""

import asyncio
import json
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

import aiohttp

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)

# HTTP status codes worth retrying.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Retry budget for transient errors.
MAX_RETRIES = 2
RETRY_DELAYS = [5, 15]


def redact_url(url: str) -> str:
    """Return scheme://host/path of ``url``, stripping any query string or fragment.

    URLs reach user-facing error strings (persisted to MongoDB, shown in the UI).
    Query strings can carry API tokens / session ids that we don't want exposed
    there — log the full URL, but only show the host/path.
    """
    if not url:
        return ""
    try:
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            return ""  # malformed; don't echo back
        return f"{p.scheme}://{p.netloc}{p.path or ''}"
    except Exception:
        return ""


def detect_provider(api_url: str) -> str:
    """Detect the AI provider from the API URL.

    Returns "anthropic" or "openai". Unknown URLs default to "openai" since most
    third-party endpoints expose an OpenAI-compatible chat-completions surface.
    """
    url_lower = api_url.lower()
    if "anthropic" in url_lower:
        return "anthropic"
    if "openai" in url_lower:
        return "openai"
    return "openai"


def build_chat_request(
    provider: str,
    model: str,
    api_key: str,
    user_prompt: str,
    system_prompt: str = "",
    max_tokens: int = 1024,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Build provider-specific (headers, body) for a chat-completion request."""
    if provider == "anthropic":
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt
    else:
        # OpenAI-compatible format. Newer OpenAI models require max_completion_tokens
        # (not max_tokens) — verified against gpt-5.x.
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        body = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": messages,
        }
    return headers, body


def extract_response_text(provider: str, result: dict) -> str:
    """Extract the assistant's text content from a provider-specific response."""
    if provider == "anthropic":
        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
    else:
        choices = result.get("choices", [])
        if choices and isinstance(choices, list):
            message = choices[0].get("message", {})
            return message.get("content", "")
        # OpenAI Responses API (newer endpoint shape).
        output = result.get("output_text", "")
        if output:
            return output
    return ""


def is_retryable(result: Dict[str, Any]) -> bool:
    """Decide whether a failed result is worth retrying."""
    error = result.get("error", "")
    for code in RETRYABLE_STATUS_CODES:
        if f"HTTP {code}" in error:
            return True
    if any(s in error for s in ("timed out", "Connection failed", "ServerDisconnectedError")):
        return True
    return False


async def call_chat_once(
    provider: str,
    api_url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    label: str = "",
) -> Dict[str, Any]:
    """Make a single chat-completion HTTP call.

    Returns ``{"raw_text": "..."}`` on success or ``{"error": "..."}`` on any
    failure (HTTP error, timeout, empty response). ``label`` is used only in
    logs to identify the caller (typically a filename or category id).
    """
    model = body.get("model", "unknown")
    timeout = aiohttp.ClientTimeout(total=60)
    tag = f" for {label}" if label else ""

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    details = [f"API returned HTTP {resp.status}"]
                    request_id = (
                        resp.headers.get("x-request-id")
                        or resp.headers.get("request-id")
                        or resp.headers.get("cf-ray")
                    )
                    if request_id:
                        details.append(f"Request-ID: {request_id}")
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        details.append(f"Retry-After: {retry_after}s")
                    rate_remaining = resp.headers.get("x-ratelimit-remaining")
                    if rate_remaining:
                        details.append(f"Rate-limit remaining: {rate_remaining}")
                    error_body = error_text[:500]
                    try:
                        error_json = json.loads(error_text)
                        msg = (
                            error_json.get("error", {}).get("message")
                            or error_json.get("message")
                            or error_json.get("detail")
                        )
                        if msg:
                            error_body = str(msg)[:500]
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    details.append(f"Response: {error_body}")
                    error_msg = " | ".join(details)
                    logger.warning(f"AI API error{tag}: {error_msg}")
                    return {"error": error_msg}

                result = await resp.json()

        raw_text = extract_response_text(provider, result)
        if not raw_text:
            logger.warning(f"AI API returned empty content{tag}")
            return {"error": "API returned empty response content"}
        return {"raw_text": raw_text}

    except asyncio.TimeoutError:
        logger.error(f"AI API call timed out{tag} (url={api_url})")
        return {"error": f"API call timed out after 60 seconds (URL: {redact_url(api_url)}, model: {model})"}
    except aiohttp.ClientConnectorError as e:
        logger.error(f"AI API connection failed{tag} (url={api_url}): {e}")
        return {"error": f"Connection failed: {e} (URL: {redact_url(api_url)})"}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"AI API call failed{tag}: {e}")
        return {"error": f"{error_type}: {str(e)[:300]}"}


async def call_chat(
    system_prompt: str,
    user_prompt: str,
    label: str = "",
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    """High-level: read runtime settings, build the request, call with retry.

    Returns ``{"raw_text": "..."}`` on success or ``{"error": "..."}`` on
    permanent failure. Caller is responsible for parsing the raw text (e.g. as
    JSON with the schema they expect).
    """
    api_url = runtime_settings.ai_api_url
    api_key = runtime_settings.ai_api_key
    model = runtime_settings.ai_model

    if not api_url or not api_key or not model:
        logger.debug("AI API not configured")
        return {"error": "AI API not configured (missing URL, key, or model)"}

    provider = detect_provider(api_url)
    headers, body = build_chat_request(
        provider, model, api_key, user_prompt, system_prompt, max_tokens=max_tokens,
    )

    result = await call_chat_once(provider, api_url, headers, body, label=label)
    if "error" not in result or not is_retryable(result):
        return result

    for attempt in range(MAX_RETRIES):
        delay = RETRY_DELAYS[attempt]

        # Honor server-supplied Retry-After if larger than our default backoff.
        error_msg = result.get("error", "")
        if "Retry-After:" in error_msg:
            try:
                ra = int(error_msg.split("Retry-After: ")[1].split("s")[0])
                delay = max(delay, ra)
            except (ValueError, IndexError):
                pass

        logger.info(
            f"AI retry {attempt + 1}/{MAX_RETRIES}{' for ' + label if label else ''} "
            f"after {delay}s (error: {error_msg[:100]})"
        )
        await asyncio.sleep(delay)

        result = await call_chat_once(provider, api_url, headers, body, label=label)
        if "error" not in result or not is_retryable(result):
            if "error" not in result:
                logger.info(f"AI retry succeeded{' for ' + label if label else ''} on attempt {attempt + 1}")
            return result

    result["error"] = f"Failed after {MAX_RETRIES + 1} attempts. Last error: {result['error']}"
    return result
