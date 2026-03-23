"""Service for generating AI summaries of documents stored in MongoDB."""

import asyncio
import json
from typing import Dict, Any, Optional

import aiohttp

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings
from app.services import mongodb_service

logger = get_logger(__name__)

# Track whether a summarization task is currently running
_running = False

# Maximum characters of extracted text to send to the AI
MAX_TEXT_LENGTH = 12000

SYSTEM_PROMPT = (
    "You are a document analysis assistant. "
    "Given the text content of a document, produce a JSON object with exactly these keys:\n"
    '  "title": a concise descriptive title for the document (5-10 words),\n'
    '  "short_summary": a brief 2-3 sentence summary of the key points,\n'
    '  "summary": a detailed 4-6 sentence summary of the document content,\n'
    '  "keywords": a list of 3-8 relevant keywords or tags,\n'
    '  "document_type": a short classification such as "report", "invoice", "manual", '
    '"contract", "letter", "spreadsheet", "presentation", "article", "form", or "other".\n'
    "Respond ONLY with valid JSON, no markdown fencing or extra text."
)

SPREADSHEET_SYSTEM_PROMPT = (
    "You are a document analysis assistant specializing in spreadsheet data. "
    "You will be given metadata about a spreadsheet file including sheet names, "
    "column headers, row counts, and sample data rows. "
    "Based on this structural information, infer the purpose and contents of the spreadsheet.\n\n"
    "Produce a JSON object with exactly these keys:\n"
    '  "title": a concise descriptive title for the spreadsheet (5-10 words),\n'
    '  "short_summary": a brief 2-3 sentence summary describing what data the spreadsheet contains and its likely purpose,\n'
    '  "summary": a detailed 4-6 sentence summary covering the structure, data types, and apparent purpose of the spreadsheet,\n'
    '  "keywords": a list of 3-8 relevant keywords or tags based on the column headers and data,\n'
    '  "document_type": always "spreadsheet".\n'
    "Respond ONLY with valid JSON, no markdown fencing or extra text."
)

# File extensions that should use the spreadsheet prompt
_SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv", ".tsv", ".ods"}


def _build_user_prompt(filename: str, text: str) -> str:
    """Build the user message for the AI API."""
    truncated = text[:MAX_TEXT_LENGTH]
    if len(text) > MAX_TEXT_LENGTH:
        truncated += "\n\n[Text truncated...]"
    return f"Document filename: {filename}\n\nDocument content:\n{truncated}"


def _parse_ai_response(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the AI response JSON, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse AI response as JSON: {text[:200]}")
        return None

    # Validate expected keys
    summary = data.get("summary")
    keywords = data.get("keywords", [])
    doc_type = data.get("document_type", "other")
    title = data.get("title", "")
    short_summary = data.get("short_summary", "")

    if not summary or not isinstance(summary, str):
        logger.warning("AI response missing valid 'summary' field")
        return None

    if not isinstance(keywords, list):
        keywords = []

    return {
        "title": str(title).strip() if title else "",
        "short_summary": str(short_summary).strip() if short_summary else "",
        "summary": summary.strip(),
        "keywords": [str(k).strip() for k in keywords if k],
        "document_type": str(doc_type).strip(),
    }


def _detect_provider(api_url: str) -> str:
    """Detect the AI provider from the API URL."""
    url_lower = api_url.lower()
    if "anthropic" in url_lower:
        return "anthropic"
    if "openai" in url_lower:
        return "openai"
    # Default to OpenAI-compatible format (most common for third-party providers)
    return "openai"


def _build_request(
    provider: str, model: str, api_key: str, filename: str, text: str,
    system_prompt: str = "",
):
    """Build provider-specific headers and body for the AI API call."""
    user_message = _build_user_prompt(filename, text)
    prompt = system_prompt or SYSTEM_PROMPT

    if provider == "anthropic":
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": model,
            "max_tokens": 1024,
            "system": prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }
    else:
        # OpenAI-compatible format
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": model,
            "max_completion_tokens": 1024,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
        }

    return headers, body


def _extract_response_text(provider: str, result: dict) -> str:
    """Extract the text content from a provider-specific API response."""
    if provider == "anthropic":
        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
    else:
        # OpenAI-compatible format
        choices = result.get("choices", [])
        if choices and isinstance(choices, list):
            message = choices[0].get("message", {})
            return message.get("content", "")
        # Also handle the newer responses API format
        output = result.get("output_text", "")
        if output:
            return output

    return ""


# HTTP status codes worth retrying
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Max retries and backoff delays (seconds) for each attempt
_MAX_RETRIES = 2
_RETRY_DELAYS = [5, 15]


def _is_retryable(result: Dict[str, Any]) -> bool:
    """Check if a failed result is worth retrying."""
    error = result.get("error", "")
    # Retryable HTTP status codes
    for code in _RETRYABLE_STATUS_CODES:
        if f"HTTP {code}" in error:
            return True
    # Retryable network/timeout errors
    if any(s in error for s in ("timed out", "Connection failed", "ServerDisconnectedError")):
        return True
    return False


async def _call_ai_api_once(
    provider: str, api_url: str, headers: dict, body: dict, filename: str,
) -> Dict[str, Any]:
    """Make a single AI API call. Returns parsed result or {"error": ...}."""
    model = body.get("model", "unknown")
    timeout = aiohttp.ClientTimeout(total=60)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    # Collect diagnostic details from response headers
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
                    # Try to extract a structured error message from the body
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
                    logger.warning(f"AI API error for {filename}: {error_msg}")
                    return {"error": error_msg}

                result = await resp.json()

        raw_text = _extract_response_text(provider, result)

        if not raw_text:
            logger.warning(f"AI API returned empty content for {filename}")
            return {"error": "API returned empty response content"}

        parsed = _parse_ai_response(raw_text)
        if parsed is None:
            logger.warning(f"Failed to parse AI response for {filename}: {raw_text[:200]}")
            return {"error": f"Failed to parse response as JSON: {raw_text[:200]}"}

        return parsed

    except asyncio.TimeoutError:
        logger.error(f"AI API call timed out for {filename}")
        return {"error": f"API call timed out after 60 seconds (URL: {api_url}, model: {model})"}
    except aiohttp.ClientConnectorError as e:
        logger.error(f"AI API connection failed for {filename}: {e}")
        return {"error": f"Connection failed: {e} (URL: {api_url})"}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"AI API call failed for {filename}: {e}")
        return {"error": f"{error_type}: {str(e)[:300]}"}


async def _call_ai_api(
    filename: str, text: str, system_prompt: str = "",
) -> Dict[str, Any]:
    """Call the configured AI API to summarize a document, with automatic retry.

    Retries up to 2 times for transient errors (429, 5xx, timeouts, connection errors)
    with increasing backoff delays. Non-retryable errors (401, 400, parse failures)
    fail immediately.

    Returns a dict with either parsed result keys (title, summary, etc.)
    or an "error" key describing what went wrong.
    """
    api_url = runtime_settings.ai_api_url
    api_key = runtime_settings.ai_api_key
    model = runtime_settings.ai_model

    if not api_url or not api_key or not model:
        logger.debug("AI API not configured, skipping summarization")
        return {"error": "AI API not configured (missing URL, key, or model)"}

    provider = _detect_provider(api_url)
    headers, body = _build_request(
        provider, model, api_key, filename, text, system_prompt=system_prompt,
    )

    result = await _call_ai_api_once(provider, api_url, headers, body, filename)

    # If successful or non-retryable, return immediately
    if "error" not in result or not _is_retryable(result):
        return result

    # Retry with backoff for transient errors
    for attempt in range(_MAX_RETRIES):
        delay = _RETRY_DELAYS[attempt]

        # Use retry-after header if present and longer than our default
        error_msg = result.get("error", "")
        if "Retry-After:" in error_msg:
            try:
                ra = int(error_msg.split("Retry-After: ")[1].split("s")[0])
                delay = max(delay, ra)
            except (ValueError, IndexError):
                pass

        logger.info(
            f"Retry {attempt + 1}/{_MAX_RETRIES} for {filename} "
            f"after {delay}s (error: {error_msg[:100]})"
        )
        await asyncio.sleep(delay)

        result = await _call_ai_api_once(provider, api_url, headers, body, filename)

        if "error" not in result or not _is_retryable(result):
            if "error" not in result:
                logger.info(f"Retry succeeded for {filename} on attempt {attempt + 1}")
            return result

    # All retries exhausted
    result["error"] = f"Failed after {_MAX_RETRIES + 1} attempts. Last error: {result['error']}"
    return result


async def summarize_pending_documents() -> Dict[str, int]:
    """
    Process all pending documents that need summarization.

    Returns a dict with counts: processed, succeeded, failed, skipped.
    """
    global _running

    if _running:
        logger.info("Summarization already running, skipping")
        return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    # Check if AI is configured
    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        logger.info("AI API not configured, skipping summarization")
        return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    _running = True
    stats = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    try:
        # Re-attempt text extraction for previously failed documents
        extraction_stats = mongodb_service.retry_text_extraction()
        if extraction_stats["succeeded"] > 0:
            logger.info(
                f"Re-extracted text for {extraction_stats['succeeded']} documents "
                f"({extraction_stats['stirling_fast']} fast, {extraction_stats['stirling_ocr']} OCR)"
            )

        # Mark documents with failed/unsupported text extraction as summary-failed
        skipped = mongodb_service.mark_unsummarizable_documents()
        if skipped > 0:
            stats["skipped"] += skipped
            logger.info(f"Marked {skipped} documents as unsummarizable (text extraction failed)")

        while True:
            pending = mongodb_service.get_pending_summaries(limit=100)
            if not pending:
                break
            logger.info(f"Found {len(pending)} documents pending summarization")

            for doc in pending:
                doc_id = doc["_id"]
                filename = doc.get("filename", "unknown")
                text = doc.get("extracted_text", "")
                extension = doc.get("extension", "")

                if not text or not text.strip():
                    mongodb_service.mark_summary_failed(
                        doc_id, error="No extracted text available"
                    )
                    stats["skipped"] += 1
                    continue

                stats["processed"] += 1

                # Use spreadsheet-specific prompt and metadata for tabular files
                prompt_text = text
                system_prompt = ""
                if extension.lower() in _SPREADSHEET_EXTENSIONS:
                    from app.services.text_extraction_service import extract_spreadsheet_metadata
                    file_data = mongodb_service.get_document_file(doc_id)
                    if file_data:
                        metadata = extract_spreadsheet_metadata(file_data, extension)
                        if metadata:
                            prompt_text = metadata
                            system_prompt = SPREADSHEET_SYSTEM_PROMPT
                            logger.debug(f"Using spreadsheet prompt for {filename}")

                result = await _call_ai_api(filename, prompt_text, system_prompt=system_prompt)

                if "error" in result:
                    mongodb_service.mark_summary_failed(doc_id, error=result["error"])
                    stats["failed"] += 1
                    logger.warning(f"Failed to summarize: {filename}")
                else:
                    mongodb_service.update_summary(
                        doc_id=doc_id,
                        summary=result["summary"],
                        keywords=result["keywords"],
                        document_type=result["document_type"],
                        model=runtime_settings.ai_model,
                        title=result.get("title", ""),
                        short_summary=result.get("short_summary", ""),
                    )
                    stats["succeeded"] += 1
                    logger.info(f"Summarized: {filename}")

                # Rate limit: small delay between API calls
                await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Summarization task error: {e}", exc_info=True)
    finally:
        _running = False

    logger.info(
        f"Summarization complete: {stats['succeeded']} succeeded, "
        f"{stats['failed']} failed, {stats['skipped']} skipped"
    )
    return stats


def is_running() -> bool:
    """Check if summarization is currently running."""
    return _running
