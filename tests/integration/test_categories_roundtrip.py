"""Real MongoDB round-trip for categorization helpers (M2 of the spec).

Stores a small fixture corpus, accepts a categorization, then exercises the
edit operations (rename, merge, delete) and checks both the persisted set and
the per-document `category` field stay consistent.
"""

import uuid
from typing import Dict, List, Tuple

import pytest

from app.services import mongodb_service

pytestmark = pytest.mark.integration


def _store_fixture_docs(scan_url: str, n: int) -> List[str]:
    """Store n empty-payload documents for the given scan; return their ids."""
    ids = []
    for i in range(n):
        payload = f"doc-{i}".encode()
        source_url = f"{scan_url}/doc-{i}-{uuid.uuid4().hex[:6]}.pdf"
        doc_id, action = mongodb_service.store_document(
            file_data=payload,
            filename=f"doc-{i}.pdf",
            extension=".pdf",
            source_url=source_url,
            source_page=scan_url,
            scan_url=scan_url,
            file_size_bytes=len(payload),
            content_type="application/pdf",
        )
        assert action == "new"
        ids.append(doc_id)
    return ids


@pytest.fixture(scope="module")
def _mongo_reachable():
    info = mongodb_service.test_connection()
    if not info["connected"]:
        pytest.skip(f"MongoDB not reachable: {info['message']}")
    return info


@pytest.fixture
def corpus(_mongo_reachable) -> Tuple[str, List[str]]:
    """Provision a clean scan with 9 fixture documents; tear it down after."""
    scan_url = f"https://example.test/cat-rt-{uuid.uuid4().hex[:8]}"
    ids = _store_fixture_docs(scan_url, 9)
    try:
        yield scan_url, ids
    finally:
        mongodb_service.delete_category_set(scan_url)
        for doc_id in ids:
            mongodb_service.delete_document(doc_id)


def _categories():
    return [
        {"name": "Financial", "description": "Money stuff"},
        {"name": "Marketing", "description": "Brand stuff"},
        {"name": "Engineering", "description": "Build stuff"},
    ]


def _assignments(ids: List[str]) -> Dict[str, str]:
    # 4 Financial, 3 Marketing, 2 Engineering
    plan = ["Financial"] * 4 + ["Marketing"] * 3 + ["Engineering"] * 2
    return dict(zip(ids, plan))


def test_accept_persists_set_and_per_doc_categories(corpus):
    scan_url, ids = corpus
    set_id = mongodb_service.accept_categorization(
        scan_url=scan_url,
        categories=_categories(),
        assignments=_assignments(ids),
        iterations_used=2,
        model="test-model",
    )
    assert isinstance(set_id, str) and set_id

    cset = mongodb_service.get_category_set(scan_url)
    assert cset is not None
    assert {c["name"] for c in cset["categories"]} == {"Financial", "Marketing", "Engineering"}
    counts = {c["name"]: c["count"] for c in cset["categories"]}
    assert counts == {"Financial": 4, "Marketing": 3, "Engineering": 2}
    assert cset["iterations_used"] == 2
    assert cset["model"] == "test-model"
    assert cset["doc_count_at_creation"] == 9

    # Per-doc field should match the assignments
    live_counts = mongodb_service.get_category_counts(scan_url)
    assert live_counts.get("Financial") == 4
    assert live_counts.get("Marketing") == 3
    assert live_counts.get("Engineering") == 2
    assert live_counts.get("", 0) == 0


def test_accept_replaces_previous_set(corpus):
    scan_url, ids = corpus
    mongodb_service.accept_categorization(
        scan_url, _categories(), _assignments(ids),
        iterations_used=1, model="m",
    )

    # Replace with a wholly different set + assignment
    new_cats = [
        {"name": "Alpha", "description": ""},
        {"name": "Beta", "description": ""},
    ]
    new_assignments = {i: ("Alpha" if idx % 2 == 0 else "Beta") for idx, i in enumerate(ids)}
    mongodb_service.accept_categorization(
        scan_url, new_cats, new_assignments,
        iterations_used=3, model="m2",
    )

    cset = mongodb_service.get_category_set(scan_url)
    assert {c["name"] for c in cset["categories"]} == {"Alpha", "Beta"}
    live_counts = mongodb_service.get_category_counts(scan_url)
    assert "Financial" not in live_counts  # cleared


def test_rename_updates_set_and_documents(corpus):
    scan_url, ids = corpus
    mongodb_service.accept_categorization(
        scan_url, _categories(), _assignments(ids),
        iterations_used=1, model="m",
    )
    moved = mongodb_service.rename_category(scan_url, "Financial", "Financial Reports")
    assert moved == 4

    cset = mongodb_service.get_category_set(scan_url)
    names = {c["name"] for c in cset["categories"]}
    assert "Financial Reports" in names and "Financial" not in names

    live_counts = mongodb_service.get_category_counts(scan_url)
    assert live_counts.get("Financial Reports") == 4
    assert live_counts.get("Financial", 0) == 0


def test_merge_collapses_source_into_target(corpus):
    scan_url, ids = corpus
    mongodb_service.accept_categorization(
        scan_url, _categories(), _assignments(ids),
        iterations_used=1, model="m",
    )
    moved = mongodb_service.merge_categories(scan_url, "Engineering", "Marketing")
    assert moved == 2

    cset = mongodb_service.get_category_set(scan_url)
    names = {c["name"] for c in cset["categories"]}
    assert names == {"Financial", "Marketing"}
    counts = {c["name"]: c["count"] for c in cset["categories"]}
    assert counts["Marketing"] == 5  # original 3 + merged 2
    assert counts["Financial"] == 4


def test_delete_category_nullifies_docs(corpus):
    scan_url, ids = corpus
    mongodb_service.accept_categorization(
        scan_url, _categories(), _assignments(ids),
        iterations_used=1, model="m",
    )
    dropped = mongodb_service.delete_category(scan_url, "Marketing")
    assert dropped == 3

    cset = mongodb_service.get_category_set(scan_url)
    assert {c["name"] for c in cset["categories"]} == {"Financial", "Engineering"}

    live_counts = mongodb_service.get_category_counts(scan_url)
    assert live_counts.get("Marketing", 0) == 0
    assert live_counts.get("", 0) == 3  # uncategorized after delete


def test_delete_category_set_clears_everything(corpus):
    scan_url, ids = corpus
    mongodb_service.accept_categorization(
        scan_url, _categories(), _assignments(ids),
        iterations_used=1, model="m",
    )
    assert mongodb_service.delete_category_set(scan_url) is True
    assert mongodb_service.get_category_set(scan_url) is None
    live_counts = mongodb_service.get_category_counts(scan_url)
    # All 9 documents should now be uncategorized
    assert live_counts.get("", 0) == 9


def test_store_document_initializes_category_fields(corpus):
    scan_url, ids = corpus
    doc = mongodb_service.get_document(ids[0])
    assert doc["category"] is None
    assert doc["categorized_at"] is None
