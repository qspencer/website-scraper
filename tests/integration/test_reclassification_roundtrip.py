"""Live round-trip for format-reclassification persistence (R-TEST-2).

Covers the path the prior eval flagged as untested: a file whose bytes don't
match its claimed extension is detected, re-extracted under the correct format,
and the corrected metadata (extension / filename / file_type_label /
original_filename) is persisted. Mirrors what the download handler does, but
exercised directly against real MongoDB so the persistence is verified, not
mocked.
"""

import uuid

import pytest

from app.services import mongodb_service
from app.services.text_extraction_service import extract_text_with_detection

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _mongo_reachable():
    info = mongodb_service.test_connection()
    if not info["connected"]:
        pytest.skip(f"MongoDB not reachable: {info['message']}")
    return info


def test_html_misnamed_as_pdf_persists_corrected_metadata(_mongo_reachable):
    scan_url = f"https://example.test/reclassify-{uuid.uuid4().hex[:8]}"
    html_bytes = (
        b"<!DOCTYPE html><html><head><title>Quarterly Notice</title></head>"
        b"<body><h1>Annual Rail Safety Report</h1>"
        b"<p>This page was served at a .pdf URL but is actually HTML.</p></body></html>"
    )
    claimed_filename = "annual-safety-report.pdf"
    claimed_ext = ".pdf"

    # 1. Detection + reroute (what downloads.py / retry_text_extraction do).
    text, status, error, actual_ext = extract_text_with_detection(html_bytes, claimed_ext)
    assert status == "complete"
    assert actual_ext == ".html"
    assert "Annual Rail Safety Report" in text

    # 2. Compute corrected metadata exactly as the download handler does, then persist.
    assert actual_ext.lower() != claimed_ext.lower()  # reclassification happened
    stem = claimed_filename.rsplit(".", 1)[0]
    stored_filename = f"{stem}{actual_ext}"
    doc_id, action = mongodb_service.store_document(
        file_data=html_bytes,
        filename=stored_filename,
        extension=actual_ext,
        source_url=f"{scan_url}/{claimed_filename}",
        source_page=scan_url,
        scan_url=scan_url,
        file_size_bytes=len(html_bytes),
        content_type="text/html",
        extracted_text=text,
        text_extraction_status=status,
        text_extraction_method="standard",
        original_filename=claimed_filename,
    )
    assert action == "new"

    try:
        # 3. Verify the persisted metadata reflects the correction.
        doc = mongodb_service.get_document(doc_id)
        assert doc is not None
        assert doc["extension"] == ".html"
        assert doc["filename"] == "annual-safety-report.html"
        assert doc["original_filename"] == "annual-safety-report.pdf"
        # file_type_label is derived from the corrected extension, not the claimed one.
        assert "pdf" not in (doc["file_type_label"] or "").lower()
        assert doc["text_extraction_status"] == "complete"
        assert doc["extracted_text"] and "Annual Rail Safety Report" in doc["extracted_text"]
    finally:
        mongodb_service.delete_document(doc_id)


def test_correctly_named_file_has_no_original_filename(_mongo_reachable):
    """A file whose extension matches its bytes is stored with original_filename=None."""
    scan_url = f"https://example.test/reclassify-{uuid.uuid4().hex[:8]}"
    text_bytes = b"plain text content, genuinely a .txt file\n"
    doc_id, _ = mongodb_service.store_document(
        file_data=text_bytes,
        filename="notes.txt",
        extension=".txt",
        source_url=f"{scan_url}/notes.txt",
        source_page=scan_url,
        scan_url=scan_url,
        file_size_bytes=len(text_bytes),
        content_type="text/plain",
        extracted_text="plain text content",
        text_extraction_status="complete",
        # original_filename omitted → should default to None
    )
    try:
        doc = mongodb_service.get_document(doc_id)
        assert doc["original_filename"] is None
        assert doc["extension"] == ".txt"
    finally:
        mongodb_service.delete_document(doc_id)
