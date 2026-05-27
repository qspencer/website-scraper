"""Live cascade-delete round-trip against real MongoDB.

Provisions a scan with documents + an accepted category set + a scan_history
row, runs DELETE through the service layer, and verifies nothing remains.
"""

import uuid

import pytest
import gridfs

from app.services import mongodb_service, history_service

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _mongo_reachable():
    info = mongodb_service.test_connection()
    if not info["connected"]:
        pytest.skip(f"MongoDB not reachable: {info['message']}")
    return info


def test_delete_scan_cascades_everything(_mongo_reachable):
    scan_url = f"https://example.test/delete-scan-{uuid.uuid4().hex[:8]}"
    doc_ids = []

    # 1. Store 5 documents under the scan
    for i in range(5):
        payload = f"document {i} payload".encode()
        did, _ = mongodb_service.store_document(
            file_data=payload,
            filename=f"doc-{i}.pdf",
            extension=".pdf",
            source_url=f"{scan_url}/doc-{i}-{uuid.uuid4().hex[:6]}.pdf",
            source_page=scan_url,
            scan_url=scan_url,
            file_size_bytes=len(payload),
            content_type="application/pdf",
        )
        doc_ids.append(did)

    # 2. Accept a category set with arbitrary assignments
    mongodb_service.accept_categorization(
        scan_url=scan_url,
        categories=[
            {"name": "A", "description": "first"},
            {"name": "B", "description": "second"},
        ],
        assignments={did: ("A" if i % 2 == 0 else "B") for i, did in enumerate(doc_ids)},
        iterations_used=1,
        model="fixture",
    )

    # 3. Write a scan_history row matching the scan_url so the cascade can clean it.
    history_service.save_scan({
        "url": scan_url, "crawl_option": "single", "max_depth": 1,
        "scan_mode": "single page", "document_filter": "common",
        "pages_scanned": 1, "documents_found": 5,
        "scan_error_count": 0, "document_error_count": 0,
    })

    # --- pre-delete sanity ---
    assert mongodb_service.get_document(doc_ids[0]) is not None
    assert mongodb_service.get_category_set(scan_url) is not None
    history_before = [h for h in history_service.get_history() if h["url"] == scan_url]
    assert len(history_before) >= 1

    # GridFS file existence check
    fs = gridfs.GridFS(mongodb_service._get_db())
    sample_doc = mongodb_service._get_collection().find_one({"scan_url": scan_url})
    assert fs.exists(sample_doc["gridfs_id"]), "GridFS file should exist before delete"

    # 4. Cascade delete
    result = mongodb_service.delete_scan(scan_url)
    history_deleted = history_service.delete_scan_by_url(scan_url)

    # --- post-delete verification ---
    assert result["documents"] == 5
    assert result["gridfs_files"] == 5
    assert result["category_set_deleted"] == 1
    assert history_deleted >= 1

    # Every doc id resolves to None
    for did in doc_ids:
        assert mongodb_service.get_document(did) is None

    # Category set gone
    assert mongodb_service.get_category_set(scan_url) is None

    # No history rows remain for this URL
    history_after = [h for h in history_service.get_history() if h["url"] == scan_url]
    assert len(history_after) == 0

    # GridFS file gone (we held a reference to the id before the delete)
    assert not fs.exists(sample_doc["gridfs_id"])


def test_delete_scan_with_no_documents_is_a_no_op(_mongo_reachable):
    """Deleting a scan_url that was never stored returns all zeros, no errors."""
    result = mongodb_service.delete_scan(f"https://nonexistent.test/{uuid.uuid4().hex}")
    assert result == {
        "documents": 0, "gridfs_files": 0, "gridfs_failed": 0, "category_set_deleted": 0,
    }
