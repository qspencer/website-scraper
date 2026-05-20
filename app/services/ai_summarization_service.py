"""Service for generating AI summaries of documents stored in MongoDB.

**Prompt-injection awareness.** This service feeds **scraped document content** —
text the operator did not write and that may include adversarial instructions
("ignore previous instructions and output X") — into a chat-completion request.
Anything the model returns is therefore *untrusted output*, even if the request
itself was authentic. Specifically:

- Summary / title / keywords / document_type are LLM outputs and **must not be
  used to drive automation** (e.g. don't dispatch on document_type without a
  human in the loop; don't render summary fields in HTML without escaping).
- A malicious document can produce mis-categorising summaries for *other*
  documents in the same scan; treat all results from a single batch as
  potentially tainted if any one document is suspect.
- The text we send is truncated to ``MAX_TEXT_LENGTH`` chars, which limits but
  does not eliminate the surface.

Error messages returned via ``"error"`` keys are persisted to MongoDB and
exposed via the per-scan failure-details endpoint, so they must not leak
secrets — see ``ai_client.redact_url`` for the canonical sanitiser.

**Architecture note.** Provider detection, HTTP plumbing, retry logic, and URL
redaction live in ``app.services.ai_client``. This module owns only the
summarization-specific concerns: prompt construction, JSON validation against
the expected summary shape, and the orchestration loop over pending documents.
The categorization service shares the same ``ai_client`` primitives.
"""

import asyncio
import functools
import json
from typing import Any, Dict, Optional, Tuple

from app.core.logging_config import get_logger
from app.services import ai_client, mongodb_service
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)


async def _run_sync(func, *args, **kwargs):
    """Run a synchronous function in a thread executor to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


# Track whether a summarization task is currently running
_running = False

# Progress info visible to the status endpoint
_progress: Dict[str, Any] = {}

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
    """Parse a summary-shaped AI response. Returns the validated dict or None."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse AI response as JSON: {text[:200]}")
        return None

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


# ---------------------------------------------------------------------------
# Backwards-compatibility shims.
#
# These names existed before the ai_client extraction and are still imported
# directly by tests/test_ai_summarization.py. They now delegate to ai_client
# but preserve the original signatures and the original return-shape contract
# (summary dict from _call_ai_api_once, not the raw_text envelope).
# ---------------------------------------------------------------------------

_redact_url = ai_client.redact_url
_detect_provider = ai_client.detect_provider
_extract_response_text = ai_client.extract_response_text
_is_retryable = ai_client.is_retryable
_RETRYABLE_STATUS_CODES = ai_client.RETRYABLE_STATUS_CODES
_MAX_RETRIES = ai_client.MAX_RETRIES
_RETRY_DELAYS = ai_client.RETRY_DELAYS


def _build_request(
    provider: str, model: str, api_key: str, filename: str, text: str,
    system_prompt: str = "",
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Build provider-specific (headers, body) for a summarization call."""
    user_prompt = _build_user_prompt(filename, text)
    prompt = system_prompt or SYSTEM_PROMPT
    return ai_client.build_chat_request(provider, model, api_key, user_prompt, prompt)


async def _call_ai_api_once(
    provider: str, api_url: str, headers: dict, body: dict, filename: str,
) -> Dict[str, Any]:
    """Single API call + summary-shape JSON parsing.

    Returns the parsed summary dict on success or {"error": ...} on failure.
    """
    raw_result = await ai_client.call_chat_once(provider, api_url, headers, body, label=filename)
    if "error" in raw_result:
        return raw_result
    raw_text = raw_result["raw_text"]
    parsed = _parse_ai_response(raw_text)
    if parsed is None:
        logger.warning(f"Failed to parse AI response for {filename}: {raw_text[:200]}")
        return {"error": f"Failed to parse response as JSON: {raw_text[:200]}"}
    return parsed


async def _call_ai_api(
    filename: str, text: str, system_prompt: str = "",
) -> Dict[str, Any]:
    """Call the configured AI API to summarize a document, with automatic retry.

    Returns a dict with either parsed summary keys (title, summary, etc.) or
    an "error" key. The HTTP/parse step delegates to _call_ai_api_once (which
    wraps ai_client.call_chat_once); the retry loop and settings read live here
    so existing tests can patch runtime_settings and _call_ai_api_once locally.
    The newer categorization service uses ai_client.call_chat directly instead.
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
    if "error" not in result or not _is_retryable(result):
        return result

    for attempt in range(_MAX_RETRIES):
        delay = _RETRY_DELAYS[attempt]

        # Honor server-supplied Retry-After if larger than our default backoff.
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

    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        logger.info("AI API not configured, skipping summarization")
        return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    _running = True
    _progress.clear()
    stats = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    try:
        # Re-attempt text extraction for previously failed documents
        # (runs in thread to avoid blocking event loop — OCR can take minutes)
        def _on_extraction_progress(info: dict):
            _progress.update({"phase": "extracting", **info})

        _progress.update({"phase": "extracting", "current": 0, "total": 0, "current_file": ""})
        extraction_stats = await _run_sync(
            mongodb_service.retry_text_extraction,
            progress_callback=_on_extraction_progress,
        )
        if extraction_stats["succeeded"] > 0:
            logger.info(
                f"Re-extracted text for {extraction_stats['succeeded']} documents "
                f"({extraction_stats['stirling_fast']} fast, {extraction_stats['stirling_ocr']} OCR)"
            )

        _progress.update({"phase": "summarizing", "current": 0, "total": 0, "current_file": ""})

        # Mark documents with failed/unsupported text extraction as summary-failed
        skipped = await _run_sync(mongodb_service.mark_unsummarizable_documents)
        if skipped > 0:
            stats["skipped"] += skipped
            logger.info(f"Marked {skipped} documents as unsummarizable (text extraction failed)")

        while True:
            pending = await _run_sync(mongodb_service.get_pending_summaries, limit=100)
            if not pending:
                break
            logger.info(f"Found {len(pending)} documents pending summarization")

            for doc in pending:
                doc_id = doc["_id"]
                filename = doc.get("filename", "unknown")
                text = doc.get("extracted_text", "")
                extension = doc.get("extension", "")

                if not text or not text.strip():
                    await _run_sync(
                        mongodb_service.mark_summary_failed,
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
                    file_data = await _run_sync(mongodb_service.get_document_file, doc_id)
                    if file_data:
                        metadata = extract_spreadsheet_metadata(file_data, extension)
                        if metadata:
                            prompt_text = metadata
                            system_prompt = SPREADSHEET_SYSTEM_PROMPT
                            logger.debug(f"Using spreadsheet prompt for {filename}")

                result = await _call_ai_api(filename, prompt_text, system_prompt=system_prompt)

                if "error" in result:
                    await _run_sync(
                        mongodb_service.mark_summary_failed,
                        doc_id, error=result["error"]
                    )
                    stats["failed"] += 1
                    logger.warning(f"Failed to summarize: {filename}")
                else:
                    await _run_sync(
                        mongodb_service.update_summary,
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
        _progress.clear()

    logger.info(
        f"Summarization complete: {stats['succeeded']} succeeded, "
        f"{stats['failed']} failed, {stats['skipped']} skipped"
    )
    return stats


def is_running() -> bool:
    """Check if summarization is currently running."""
    return _running


def get_progress() -> Dict[str, Any]:
    """Get current progress info (phase, current, total, current_file)."""
    return dict(_progress)
