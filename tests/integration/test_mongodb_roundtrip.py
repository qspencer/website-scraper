"""Real MongoDB round-trip — verifies the GridFS storage path against localhost:27017.

The 57 mocked tests in tests/test_mongodb.py cover the helper internals; these
verify the production path actually persists and retrieves bytes correctly. A
refactor that drops _get_db() in favor of an injected client should still pass
this file — that's the point.
"""

import uuid

import pytest

from app.services import mongodb_service

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _mongo_reachable():
    """Skip the module if MongoDB isn't actually up."""
    info = mongodb_service.test_connection()
    if not info["connected"]:
        pytest.skip(f"MongoDB not reachable: {info['message']}")
    return info


@pytest.fixture
def doc_id(_mongo_reachable):
    """Store a document and yield its id; delete on teardown so the DB stays clean."""
    payload = b"Wave 4 integration test payload " + uuid.uuid4().bytes
    scan_url = f"https://example.test/scan-{uuid.uuid4().hex[:8]}"
    source_url = f"{scan_url}/doc-{uuid.uuid4().hex[:8]}.pdf"
    inserted_id, action = mongodb_service.store_document(
        file_data=payload,
        filename="wave4-roundtrip.pdf",
        extension=".pdf",
        source_url=source_url,
        source_page=scan_url,
        scan_url=scan_url,
        file_size_bytes=len(payload),
        content_type="application/pdf",
        extracted_text=None,
    )
    assert action == "new"
    yield inserted_id, payload, scan_url, source_url
    mongodb_service.delete_document(inserted_id)


def test_store_then_get_metadata_round_trip(doc_id):
    inserted_id, payload, scan_url, source_url = doc_id
    meta = mongodb_service.get_document(inserted_id)
    assert meta is not None
    assert meta["filename"] == "wave4-roundtrip.pdf"
    assert meta["source_url"] == source_url
    assert meta["scan_url"] == scan_url
    assert meta["file_size_bytes"] == len(payload)


def test_store_then_get_file_bytes_round_trip(doc_id):
    inserted_id, payload, _, _ = doc_id
    fetched = mongodb_service.get_document_file(inserted_id)
    assert fetched == payload, "GridFS round-trip must preserve bytes exactly"


def test_store_then_search_finds_document(doc_id):
    inserted_id, _, scan_url, _ = doc_id
    results = mongodb_service.search_documents(scan_url=scan_url, limit=10)
    assert len(results) >= 1
    ids = {r["_id"] for r in results}  # already stringified by search_documents
    assert inserted_id in ids


def test_delete_removes_document(_mongo_reachable):
    """Explicit delete test (the doc_id fixture's teardown also exercises this)."""
    payload = b"ephemeral payload"
    scan_url = f"https://example.test/scan-{uuid.uuid4().hex[:8]}"
    inserted_id, _ = mongodb_service.store_document(
        file_data=payload,
        filename="to-delete.pdf",
        extension=".pdf",
        source_url=f"{scan_url}/d.pdf",
        source_page=scan_url,
        scan_url=scan_url,
        file_size_bytes=len(payload),
        content_type="application/pdf",
    )
    assert mongodb_service.get_document(inserted_id) is not None
    assert mongodb_service.delete_document(inserted_id) is True
    assert mongodb_service.get_document(inserted_id) is None
