"""HTTP-level tests for app.api.routes.categorize (M4).

Uses TestClient. The pipeline itself is mocked; we test session bookkeeping,
SSE event marshaling, and the CRUD helpers' wire-up.
"""

import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.services import categorization_service as cs
from app.services import mongodb_service
from app.services.session_store import categorize_sessions


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    yield
    categorize_sessions.clear()


# --- /categorize/start -----------------------------------------------------


class TestCategorizeStart:

    def test_rejects_missing_scan_url(self, client):
        resp = client.post("/api/download/mongodb/categorize/start?scan_url=")
        assert resp.status_code == 400
        assert "scan_url" in resp.json()["detail"]

    @patch.object(cs, "run_categorization_pipeline", new_callable=AsyncMock)
    def test_start_creates_session_and_returns_id(self, mock_run, client):
        async def _fake_pipeline(scan_url, emit=None):
            return {
                "scan_url": scan_url, "categories": [], "assignments": {},
                "quality": {"ok": True, "signals": [], "distribution": {}, "other_count": 0},
                "iterations_used": 1, "model": "m", "doc_count": 0,
            }
        mock_run.side_effect = _fake_pipeline

        resp = client.post("/api/download/mongodb/categorize/start?scan_url=https://x.test/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["session_id"]
        assert body["session_id"] in categorize_sessions


# --- /categorize/progress (SSE) -------------------------------------------


class TestCategorizeProgress:

    def test_unknown_session_returns_404(self, client):
        resp = client.get("/api/download/mongodb/categorize/progress/unknown-id")
        assert resp.status_code == 404

    def test_sse_stream_emits_then_closes(self, client):
        """Pre-seed a session whose queue already has events + the sentinel."""
        from app.api.routes.categorize import _END_SENTINEL

        session_id = "test-session-progress"
        q = asyncio.Queue()
        # Push some events synchronously (Queue.put_nowait is fine here)
        q.put_nowait({"type": "iteration_start", "data": {"iteration": 1}})
        q.put_nowait({"type": "final", "data": {"ok": True}})
        q.put_nowait(_END_SENTINEL)

        categorize_sessions[session_id] = {
            "status": "complete", "queue": q, "scan_url": "https://x", "result": None,
        }

        with client.stream("GET", f"/api/download/mongodb/categorize/progress/{session_id}") as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

        assert "event: iteration_start" in body
        assert "event: final" in body
        # No trailing events after sentinel
        assert body.count("event: ") == 2


# --- /categorize/accept ----------------------------------------------------


class TestCategorizeAccept:

    def test_unknown_session_returns_404(self, client):
        resp = client.post("/api/download/mongodb/categorize/accept/unknown-id")
        assert resp.status_code == 404

    def test_rejects_session_not_complete(self, client):
        sid = "in-flight"
        categorize_sessions[sid] = {
            "status": "running", "queue": asyncio.Queue(),
            "scan_url": "https://x", "result": None, "error": None, "task": None,
        }
        resp = client.post(f"/api/download/mongodb/categorize/accept/{sid}")
        assert resp.status_code == 400
        assert "running" in resp.json()["detail"]

    @patch.object(mongodb_service, "accept_categorization", return_value="set-id-123")
    def test_complete_session_persists_and_drops(self, mock_accept, client):
        sid = "done"
        result = {
            "scan_url": "https://x.test/scan",
            "categories": [{"name": "A", "description": "d"}],
            "assignments": {"doc1": "A"},
            "iterations_used": 2,
            "model": "test-m",
            "doc_count": 1,
            "quality": {"ok": True, "signals": [], "distribution": {"A": 1}, "other_count": 0},
        }
        categorize_sessions[sid] = {
            "status": "complete", "queue": asyncio.Queue(),
            "scan_url": "https://x.test/scan", "result": result,
            "error": None, "task": None,
        }

        resp = client.post(f"/api/download/mongodb/categorize/accept/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["set_id"] == "set-id-123"
        assert body["scan_url"] == "https://x.test/scan"
        assert sid not in categorize_sessions  # dropped after persist
        mock_accept.assert_called_once()
        call_kwargs = mock_accept.call_args.kwargs
        assert call_kwargs["scan_url"] == "https://x.test/scan"
        assert call_kwargs["iterations_used"] == 2


# --- /categorize/cancel ----------------------------------------------------


class TestCategorizeCancel:

    def test_unknown_session_returns_404(self, client):
        resp = client.post("/api/download/mongodb/categorize/cancel/unknown-id")
        assert resp.status_code == 404

    def test_cancel_calls_task_cancel(self, client):
        sid = "cancellable"
        task = MagicMock()
        task.done.return_value = False
        categorize_sessions[sid] = {
            "status": "running", "queue": asyncio.Queue(),
            "scan_url": "https://x", "result": None, "error": None,
            "task": task,
        }
        resp = client.post(f"/api/download/mongodb/categorize/cancel/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        task.cancel.assert_called_once()

    def test_cancel_done_session_emits_sentinel(self, client):
        from app.api.routes.categorize import _END_SENTINEL

        sid = "already-done"
        task = MagicMock()
        task.done.return_value = True  # task finished
        q = asyncio.Queue()
        categorize_sessions[sid] = {
            "status": "complete", "queue": q, "scan_url": "https://x",
            "result": None, "error": None, "task": task,
        }
        resp = client.post(f"/api/download/mongodb/categorize/cancel/{sid}")
        assert resp.status_code == 200
        task.cancel.assert_not_called()
        # Sentinel pushed so any SSE consumer can drain and close
        assert q.get_nowait() is _END_SENTINEL


# --- GET /categories -------------------------------------------------------


class TestGetCategories:

    def test_rejects_missing_scan_url(self, client):
        resp = client.get("/api/download/mongodb/categories?scan_url=")
        assert resp.status_code == 400

    @patch.object(mongodb_service, "get_category_set", return_value=None)
    def test_missing_set_returns_404(self, mock_get, client):
        resp = client.get("/api/download/mongodb/categories?scan_url=https://x")
        assert resp.status_code == 404

    @patch.object(mongodb_service, "get_category_counts")
    @patch.object(mongodb_service, "get_category_set")
    def test_returns_set_with_live_counts(self, mock_get, mock_counts, client):
        mock_get.return_value = {
            "scan_url": "https://x.test/scan",
            "categories": [{"name": "A", "description": "d", "count": 3}],
            "iterations_used": 2,
            "model": "m",
            "doc_count_at_creation": 5,
        }
        mock_counts.return_value = {"A": 3, "": 2}

        resp = client.get("/api/download/mongodb/categories?scan_url=https://x.test/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scan_url"] == "https://x.test/scan"
        assert body["live_counts"]["A"] == 3
        assert body["uncategorized_count"] == 2


# --- PATCH /categories -----------------------------------------------------


class TestPatchCategories:

    @patch.object(mongodb_service, "rename_category", return_value=4)
    def test_rename_round_trip(self, mock_rename, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "rename", "name": "Old", "new_name": "New"},
        )
        assert resp.status_code == 200
        assert resp.json()["documents_affected"] == 4
        mock_rename.assert_called_once_with("https://x", "Old", "New")

    @patch.object(mongodb_service, "merge_categories", return_value=2)
    def test_merge_round_trip(self, mock_merge, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "merge", "name": "Source", "target": "Target"},
        )
        assert resp.status_code == 200
        assert resp.json()["documents_affected"] == 2
        mock_merge.assert_called_once_with("https://x", "Source", "Target")

    @patch.object(mongodb_service, "delete_category", return_value=7)
    def test_delete_round_trip(self, mock_delete, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "delete", "name": "Doomed"},
        )
        assert resp.status_code == 200
        assert resp.json()["documents_affected"] == 7
        mock_delete.assert_called_once_with("https://x", "Doomed")

    def test_rename_without_new_name_is_422(self, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "rename", "name": "Old"},  # new_name missing
        )
        assert resp.status_code == 422  # pydantic validation error

    def test_merge_without_target_is_422(self, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "merge", "name": "Source"},  # target missing
        )
        assert resp.status_code == 422

    @patch.object(mongodb_service, "rename_category", side_effect=ValueError("collision"))
    def test_value_error_becomes_400(self, mock_rename, client):
        resp = client.patch(
            "/api/download/mongodb/categories?scan_url=https://x",
            json={"action": "rename", "name": "A", "new_name": "B"},
        )
        assert resp.status_code == 400
        assert "collision" in resp.json()["detail"]


# --- DELETE /categories ----------------------------------------------------


class TestDeleteCategories:

    @patch.object(mongodb_service, "delete_category_set", return_value=True)
    def test_delete_round_trip(self, mock_del, client):
        resp = client.delete("/api/download/mongodb/categories?scan_url=https://x.test/scan")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_del.assert_called_once_with("https://x.test/scan")

    @patch.object(mongodb_service, "delete_category_set", return_value=False)
    def test_delete_no_set_returns_404(self, mock_del, client):
        resp = client.delete("/api/download/mongodb/categories?scan_url=https://x")
        assert resp.status_code == 404

    def test_delete_missing_scan_url_is_422(self, client):
        # FastAPI returns 422 when a required query parameter is missing.
        resp = client.delete("/api/download/mongodb/categories")
        assert resp.status_code == 422
