"""Shared fixtures for the integration tier.

These tests hit real backends (MongoDB, AI API, Stirling-PDF, Playwright).
They are excluded from the default pytest run; opt in with `pytest -m integration`.

The top-level tests/conftest.py redirects DB_PATH to a temp file *before* app code
imports, so runtime_settings start from defaults (empty AI key/model, default Mongo
URI, default Stirling URL). For the integration tier we want the developer's real
configuration. The autouse session fixture below reads the production scraper.db
(if it exists) and overlays its AI/Mongo/Stirling values onto runtime_settings.
"""

import json
import os
import sqlite3

import pytest

_PRODUCTION_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scraper.db"
)
_OVERLAY_KEYS = (
    "ai_api_url", "ai_api_key", "ai_model",
    "mongodb_uri", "mongodb_database",
    "stirling_pdf_url",
)


@pytest.fixture(scope="session", autouse=True)
def _overlay_production_settings():
    """Copy AI / Mongo / Stirling settings from the developer's real scraper.db
    into the in-memory runtime_settings used during integration tests."""
    if not os.path.exists(_PRODUCTION_DB):
        return
    try:
        conn = sqlite3.connect(_PRODUCTION_DB)
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN ({})".format(
                    ",".join("?" * len(_OVERLAY_KEYS))
                ),
                _OVERLAY_KEYS,
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return

    from app.services.settings_service import runtime_settings
    for key, raw in rows:
        try:
            value = json.loads(raw)  # values are stored JSON-encoded
        except (json.JSONDecodeError, TypeError):
            value = raw
        # Bypass setters' clamps/validators: we want the developer's real value verbatim.
        runtime_settings._settings[key] = value


def build_minimal_pdf(text: str) -> bytes:
    """Build a tiny but valid single-page PDF with one line of selectable text.

    Returns ~400 bytes of PDF. Used by the Stirling-PDF round-trip test to verify
    that the configured Stirling endpoint extracts text from a real PDF we control.
    Avoids needing a binary fixture file or a PDF-writer dependency.
    """
    safe_text = text.encode("ascii", errors="replace")
    content_stream = b"BT /F1 24 Tf 72 700 Td (" + safe_text + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content_stream), content_stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_offset = len(body)
    body += b"xref\n0 %d\n" % (len(objects) + 1)
    body += b"0000000000 65535 f \n"
    for off in offsets:
        body += b"%010d 00000 n \n" % off
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n" % (
        len(objects) + 1, xref_offset
    )
    return body
