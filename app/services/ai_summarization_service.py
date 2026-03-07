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
    '  "summary": a concise 2-4 sentence summary of the document content,\n'
    '  "keywords": a list of 3-8 relevant keywords or tags,\n'
    '  "document_type": a short classification such as "report", "invoice", "manual", '
    '"contract", "letter", "spreadsheet", "presentation", "article", "form", or "other".\n'
    "Respond ONLY with valid JSON, no markdown fencing or extra text."
)


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

    if not summary or not isinstance(summary, str):
        logger.warning("AI response missing valid 'summary' field")
        return None

    if not isinstance(keywords, list):
        keywords = []

    return {
        "summary": summary.strip(),
        "keywords": [str(k).strip() for k in keywords if k],
        "document_type": str(doc_type).strip(),
    }


async def _call_ai_api(filename: str, text: str) -> Optional[Dict[str, Any]]:
    """Call the configured AI API to summarize a document."""
    api_url = runtime_settings.ai_api_url
    api_key = runtime_settings.ai_api_key
    model = runtime_settings.ai_model

    if not api_url or not api_key or not model:
        logger.debug("AI API not configured, skipping summarization")
        return None

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    body = {
        "model": model,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_prompt(filename, text)},
        ],
    }

    timeout = aiohttp.ClientTimeout(total=60)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning(
                        f"AI API returned {resp.status} for {filename}: {error_text[:200]}"
                    )
                    return None

                result = await resp.json()

        # Extract text from Anthropic Messages API response format
        content = result.get("content", [])
        if content and isinstance(content, list):
            raw_text = content[0].get("text", "")
        else:
            raw_text = ""

        if not raw_text:
            logger.warning(f"AI API returned empty content for {filename}")
            return None

        return _parse_ai_response(raw_text)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"AI API call failed for {filename}: {e}")
        return None


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
        pending = mongodb_service.get_pending_summaries(limit=100)
        logger.info(f"Found {len(pending)} documents pending summarization")

        for doc in pending:
            doc_id = doc["_id"]
            filename = doc.get("filename", "unknown")
            text = doc.get("extracted_text", "")

            if not text or not text.strip():
                mongodb_service.mark_summary_failed(doc_id)
                stats["skipped"] += 1
                continue

            stats["processed"] += 1

            result = await _call_ai_api(filename, text)

            if result:
                mongodb_service.update_summary(
                    doc_id=doc_id,
                    summary=result["summary"],
                    keywords=result["keywords"],
                    document_type=result["document_type"],
                    model=runtime_settings.ai_model,
                )
                stats["succeeded"] += 1
                logger.info(f"Summarized: {filename}")
            else:
                mongodb_service.mark_summary_failed(doc_id)
                stats["failed"] += 1
                logger.warning(f"Failed to summarize: {filename}")

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
