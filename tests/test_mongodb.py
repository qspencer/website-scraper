"""Tests for MongoDB service and download endpoints."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from bson import ObjectId

from app.services import mongodb_service
from app.api.routes.scraper import scrape_sessions
from app.api.routes.downloads import download_sessions
from app.schemas.scrape import ScrapeRequest, ScrapeResult
from app.schemas.document import DocumentInfo
from app.core.constants import DocumentTypeFilter, CrawlDepthOption


def _make_doc(url="https://example.com/file.pdf", filename=None, ext=".pdf"):
    if filename is None:
        filename = url.rsplit("/", 1)[-1]
    return DocumentInfo(
        url=url,
        filename=filename,
        extension=ext,
        source_page="https://example.com",
        depth=0,
    )


def _setup_scrape_session(session_id="test-session"):
    """Create a scrape session with results."""
    docs = [
        _make_doc("https://example.com/a.pdf"),
        _make_doc("https://example.com/b.docx", ext=".docx"),
    ]
    request = ScrapeRequest(
        url="https://example.com",
        crawl_option=CrawlDepthOption.SINGLE_PAGE,
        document_filter=DocumentTypeFilter.ALL,
    )
    result = ScrapeResult(
        success=True,
        documents=docs,
        pages_scanned=1,
    )
    scrape_sessions[session_id] = {
        "request": request,
        "result": result,
        "status": "complete",
    }
    return session_id, docs


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    download_sessions.clear()
    scrape_sessions.clear()


# ---------------------------------------------------------------------------
# MongoDB service unit tests (all mocked - no real MongoDB needed)
# ---------------------------------------------------------------------------


class TestMongoDBService:
    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.return_value = {"ok": 1}
        mock_get_client.return_value = mock_client

        mock_db = MagicMock()
        mock_db.__getitem__.return_value.count_documents.return_value = 42

        with patch.object(mongodb_service, "_get_db", return_value=mock_db):
            result = mongodb_service.test_connection()

        assert result["connected"] is True
        assert result["document_count"] == 42
        assert "42" in result["message"]
        assert result["help_url"] is None

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_refused(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception(
            "localhost:27017: [Errno 111] Connection refused"
        )
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "not running" in result["message"]
        assert "install" in result["message"].lower()
        assert result["help_url"] is not None
        assert result["document_count"] == 0

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_auth_failed(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception("Authentication failed")
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "authentication" in result["message"].lower()
        assert result["help_url"] is not None

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_timeout(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception("connection timed out")
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "timed out" in result["message"].lower()

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_host_not_found(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception("Name or service not known")
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "could not reach" in result["message"].lower()

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_ssl_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception("SSL handshake failed")
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "ssl" in result["message"].lower()

    @patch.object(mongodb_service, "_get_client")
    def test_test_connection_generic_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = Exception("something unexpected")
        mock_get_client.return_value = mock_client

        result = mongodb_service.test_connection()

        assert result["connected"] is False
        assert "something unexpected" in result["message"]
        assert result["help_url"] is not None

    @patch.object(mongodb_service, "_ensure_indexes")
    @patch.object(mongodb_service, "_get_collection")
    @patch.object(mongodb_service, "_get_db")
    def test_store_document(self, mock_get_db, mock_get_col, mock_indexes):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_fs = MagicMock()
        mock_fs.put.return_value = ObjectId()

        mock_col = MagicMock()
        mock_col.find_one.return_value = None  # No existing document
        mock_col.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        mock_get_col.return_value = mock_col

        with patch("gridfs.GridFS", return_value=mock_fs):
            doc_id, action = mongodb_service.store_document(
                file_data=b"test content",
                filename="test.pdf",
                extension=".pdf",
                source_url="https://example.com/test.pdf",
                source_page="https://example.com",
                scan_url="https://example.com",
                file_size_bytes=12,
                content_type="application/pdf",
                extracted_text="extracted text here",
                text_extraction_status="complete",
            )

        assert doc_id is not None
        assert action == "new"
        mock_fs.put.assert_called_once()
        mock_col.insert_one.assert_called_once()

        # Verify the document structure
        stored_doc = mock_col.insert_one.call_args[0][0]
        assert stored_doc["filename"] == "test.pdf"
        assert stored_doc["extension"] == ".pdf"
        assert stored_doc["extracted_text"] == "extracted text here"
        assert stored_doc["summary"] is None
        assert stored_doc["summary_status"] == "pending"

    @patch.object(mongodb_service, "_get_collection")
    @patch.object(mongodb_service, "_get_db")
    def test_store_document_replaces_duplicate(self, mock_get_db, mock_get_col):
        """Re-downloading the same document replaces the existing one."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        existing_id = ObjectId()
        old_gridfs_id = ObjectId()

        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "_id": existing_id,
            "gridfs_id": old_gridfs_id,
            "filename": "test.pdf",
        }
        mock_get_col.return_value = mock_col

        new_gridfs_id = ObjectId()
        mock_fs = MagicMock()
        mock_fs.put.return_value = new_gridfs_id

        with patch("gridfs.GridFS", return_value=mock_fs):
            doc_id, action = mongodb_service.store_document(
                file_data=b"updated content",
                filename="test.pdf",
                extension=".pdf",
                source_url="https://example.com/test.pdf",
                source_page="https://example.com",
                scan_url="https://example.com",
                file_size_bytes=15,
                content_type="application/pdf",
                extracted_text="new extracted text",
                text_extraction_status="complete",
            )

        # Should return the existing document's ID and "updated" action
        assert doc_id == str(existing_id)
        assert action == "updated"

        # Should delete the old GridFS file
        mock_fs.delete.assert_called_once_with(old_gridfs_id)

        # Should update, not insert
        mock_col.insert_one.assert_not_called()
        mock_col.update_one.assert_called_once()

        # Verify the update resets summary fields
        update_set = mock_col.update_one.call_args[0][1]["$set"]
        assert update_set["extracted_text"] == "new extracted text"
        assert update_set["summary"] is None
        assert update_set["summary_status"] == "pending"
        assert update_set["gridfs_id"] == new_gridfs_id

    @patch.object(mongodb_service, "_get_collection")
    def test_get_document(self, mock_get_col):
        oid = ObjectId()
        gridfs_oid = ObjectId()
        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "_id": oid,
            "gridfs_id": gridfs_oid,
            "filename": "test.pdf",
        }
        mock_get_col.return_value = mock_col

        doc = mongodb_service.get_document(str(oid))

        assert doc is not None
        assert doc["_id"] == str(oid)
        assert doc["gridfs_id"] == str(gridfs_oid)

    @patch.object(mongodb_service, "_get_collection")
    def test_get_document_not_found(self, mock_get_col):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_get_col.return_value = mock_col

        doc = mongodb_service.get_document(str(ObjectId()))
        assert doc is None

    @patch.object(mongodb_service, "_get_collection")
    @patch.object(mongodb_service, "_get_db")
    def test_get_document_file(self, mock_get_db, mock_get_col):
        oid = ObjectId()
        gridfs_oid = ObjectId()

        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "_id": oid,
            "gridfs_id": gridfs_oid,
        }
        mock_get_col.return_value = mock_col

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_grid_out = MagicMock()
        mock_grid_out.read.return_value = b"file bytes"

        mock_fs = MagicMock()
        mock_fs.get.return_value = mock_grid_out

        with patch("gridfs.GridFS", return_value=mock_fs):
            data = mongodb_service.get_document_file(str(oid))

        assert data == b"file bytes"

    @patch.object(mongodb_service, "_get_collection")
    def test_get_document_count(self, mock_get_col):
        mock_col = MagicMock()
        mock_col.count_documents.return_value = 10
        mock_get_col.return_value = mock_col

        count = mongodb_service.get_document_count()
        assert count == 10

    @patch.object(mongodb_service, "_get_collection")
    def test_get_document_count_error(self, mock_get_col):
        mock_get_col.side_effect = Exception("no connection")
        count = mongodb_service.get_document_count()
        assert count == 0

    @patch.object(mongodb_service, "_get_collection")
    @patch.object(mongodb_service, "_get_db")
    def test_delete_document(self, mock_get_db, mock_get_col):
        oid = ObjectId()
        gridfs_oid = ObjectId()

        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "_id": oid,
            "gridfs_id": gridfs_oid,
        }
        mock_get_col.return_value = mock_col

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_fs = MagicMock()

        with patch("gridfs.GridFS", return_value=mock_fs):
            result = mongodb_service.delete_document(str(oid))

        assert result is True
        mock_fs.delete.assert_called_once_with(gridfs_oid)
        mock_col.delete_one.assert_called_once()

    @patch.object(mongodb_service, "_get_collection")
    def test_delete_document_not_found(self, mock_get_col):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_get_col.return_value = mock_col

        result = mongodb_service.delete_document(str(ObjectId()))
        assert result is False

    @patch.object(mongodb_service, "_get_collection")
    def test_search_documents_by_text(self, mock_get_col):
        oid = ObjectId()
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value.limit.return_value = [
            {
                "_id": oid,
                "gridfs_id": ObjectId(),
                "filename": "report.pdf",
                "extracted_text": "This is a long document about finance " + "x" * 300,
            }
        ]

        mock_col = MagicMock()
        mock_col.find.return_value = mock_cursor
        mock_get_col.return_value = mock_col

        results = mongodb_service.search_documents(query="finance")

        assert len(results) == 1
        assert results[0]["_id"] == str(oid)
        assert "extracted_text_preview" in results[0]
        assert "extracted_text" not in results[0]

    @patch.object(mongodb_service, "_get_collection")
    def test_search_documents_no_query(self, mock_get_col):
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value.limit.return_value = []

        mock_col = MagicMock()
        mock_col.find.return_value = mock_cursor
        mock_get_col.return_value = mock_col

        results = mongodb_service.search_documents()
        assert results == []


# ---------------------------------------------------------------------------
# MongoDB download API endpoint tests
# ---------------------------------------------------------------------------


class TestMongoDBEndpoints:
    @patch.object(mongodb_service, "test_connection")
    def test_mongodb_status(self, mock_test, client):
        mock_test.return_value = {
            "connected": True,
            "message": "Connected (5 documents stored)",
            "document_count": 5,
            "help_url": None,
        }
        resp = client.get("/api/download/mongodb/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["help_url"] is None

    @patch.object(mongodb_service, "test_connection")
    def test_mongodb_status_disconnected(self, mock_test, client):
        mock_test.return_value = {
            "connected": False,
            "message": "MongoDB is not running. Please install and start MongoDB, then try again.",
            "document_count": 0,
            "help_url": "https://www.mongodb.com/docs/manual/installation/",
        }
        resp = client.get("/api/download/mongodb/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["help_url"] is not None

    @patch.object(mongodb_service, "test_connection")
    def test_start_mongodb_download(self, mock_test, client):
        mock_test.return_value = {"connected": True, "message": "OK", "document_count": 0}
        session_id, docs = _setup_scrape_session()

        resp = client.post("/api/download/mongodb/start", json={
            "session_id": session_id,
            "document_urls": [docs[0].url],
            "download_path": "mongodb",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["session_id"]

    @patch.object(mongodb_service, "test_connection")
    def test_start_mongodb_download_not_connected(self, mock_test, client):
        mock_test.return_value = {"connected": False, "message": "refused", "document_count": 0}
        session_id, docs = _setup_scrape_session()

        resp = client.post("/api/download/mongodb/start", json={
            "session_id": session_id,
            "document_urls": [docs[0].url],
            "download_path": "mongodb",
        })
        assert resp.status_code == 400
        assert "not available" in resp.json()["detail"]

    @patch.object(mongodb_service, "test_connection")
    def test_start_mongodb_download_no_session(self, mock_test, client):
        mock_test.return_value = {"connected": True, "message": "OK", "document_count": 0}
        resp = client.post("/api/download/mongodb/start", json={
            "session_id": "nonexistent",
            "document_urls": ["https://example.com/a.pdf"],
            "download_path": "mongodb",
        })
        assert resp.status_code == 404

    @patch.object(mongodb_service, "test_connection")
    def test_start_mongodb_download_no_matching_docs(self, mock_test, client):
        mock_test.return_value = {"connected": True, "message": "OK", "document_count": 0}
        session_id, _ = _setup_scrape_session()

        resp = client.post("/api/download/mongodb/start", json={
            "session_id": session_id,
            "document_urls": ["https://example.com/nonexistent.pdf"],
            "download_path": "mongodb",
        })
        assert resp.status_code == 400
        assert "No valid" in resp.json()["detail"]

    def test_mongodb_progress_no_session(self, client):
        resp = client.get("/api/download/mongodb/progress/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Document search API endpoint tests
# ---------------------------------------------------------------------------


class TestDocumentSearchEndpoints:
    @patch.object(mongodb_service, "search_documents")
    def test_search_no_params(self, mock_search, client):
        mock_search.return_value = [
            {"_id": "abc", "filename": "test.pdf", "summary": "A test doc"},
        ]
        resp = client.get("/api/download/mongodb/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["filename"] == "test.pdf"
        mock_search.assert_called_once_with(
            query="", scan_url=None, extension=None, category=None, limit=50,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_with_query(self, mock_search, client):
        mock_search.return_value = []
        resp = client.get("/api/download/mongodb/search?q=finance&extension=.pdf&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        mock_search.assert_called_once_with(
            query="finance", scan_url=None, extension=".pdf", category=None, limit=10,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_with_scan_url(self, mock_search, client):
        mock_search.return_value = []
        resp = client.get("/api/download/mongodb/search?scan_url=https://example.com")
        assert resp.status_code == 200
        mock_search.assert_called_once_with(
            query="", scan_url="https://example.com", extension=None, category=None, limit=50,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_limit_capped(self, mock_search, client):
        mock_search.return_value = []
        resp = client.get("/api/download/mongodb/search?limit=999")
        assert resp.status_code == 200
        mock_search.assert_called_once_with(
            query="", scan_url=None, extension=None, category=None, limit=999,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_with_category(self, mock_search, client):
        """M6: category filter passed through as-is."""
        mock_search.return_value = []
        resp = client.get("/api/download/mongodb/search?scan_url=https://x&category=Financial%20Reports")
        assert resp.status_code == 200
        mock_search.assert_called_once_with(
            query="", scan_url="https://x", extension=None,
            category="Financial Reports", limit=50,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_with_uncategorized_sentinel(self, mock_search, client):
        """M6: __uncategorized__ sentinel passes through to the service layer."""
        mock_search.return_value = []
        resp = client.get("/api/download/mongodb/search?scan_url=https://x&category=__uncategorized__")
        assert resp.status_code == 200
        mock_search.assert_called_once_with(
            query="", scan_url="https://x", extension=None,
            category="__uncategorized__", limit=50,
        )

    @patch.object(mongodb_service, "search_documents")
    def test_search_error(self, mock_search, client):
        mock_search.side_effect = Exception("MongoDB down")
        resp = client.get("/api/download/mongodb/search?q=test")
        assert resp.status_code == 500

    @patch.object(mongodb_service, "_get_collection")
    def test_get_scans(self, mock_get_col, client):
        from datetime import datetime, timezone
        mock_col = MagicMock()
        mock_col.aggregate.return_value = [
            {"_id": "https://example.com", "count": 10, "latest": datetime(2026, 1, 1, tzinfo=timezone.utc)},
            {"_id": "https://other.com", "count": 5, "latest": datetime(2025, 12, 1, tzinfo=timezone.utc)},
        ]
        mock_get_col.return_value = mock_col

        resp = client.get("/api/download/mongodb/scans")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scans"]) == 2
        assert data["scans"][0]["scan_url"] == "https://example.com"
        assert data["scans"][0]["document_count"] == 10

    @patch.object(mongodb_service, "_get_collection")
    def test_get_scans_empty(self, mock_get_col, client):
        mock_col = MagicMock()
        mock_col.aggregate.return_value = []
        mock_get_col.return_value = mock_col

        resp = client.get("/api/download/mongodb/scans")
        assert resp.status_code == 200
        assert resp.json()["scans"] == []

    @patch.object(mongodb_service, "_get_collection")
    def test_get_scans_error(self, mock_get_col, client):
        mock_get_col.side_effect = Exception("no connection")
        resp = client.get("/api/download/mongodb/scans")
        assert resp.status_code == 200
        assert resp.json()["scans"] == []

    @patch.object(mongodb_service, "_get_collection")
    def test_get_extensions(self, mock_get_col, client):
        mock_col = MagicMock()
        mock_col.distinct.return_value = [".csv", ".docx", ".pdf"]
        mock_get_col.return_value = mock_col

        resp = client.get("/api/download/mongodb/extensions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["extensions"] == [".csv", ".docx", ".pdf"]
        mock_col.distinct.assert_called_once_with("extension", {})

    @patch.object(mongodb_service, "_get_collection")
    def test_get_extensions_filtered_by_scan(self, mock_get_col, client):
        mock_col = MagicMock()
        mock_col.distinct.return_value = [".pdf"]
        mock_get_col.return_value = mock_col

        resp = client.get("/api/download/mongodb/extensions?scan_url=https://example.com")
        assert resp.status_code == 200
        assert resp.json()["extensions"] == [".pdf"]
        mock_col.distinct.assert_called_once_with("extension", {"scan_url": "https://example.com"})

    @patch.object(mongodb_service, "_get_collection")
    def test_get_extensions_error(self, mock_get_col, client):
        mock_get_col.side_effect = Exception("no connection")
        resp = client.get("/api/download/mongodb/extensions")
        assert resp.status_code == 200
        assert resp.json()["extensions"] == []

    @patch.object(mongodb_service, "get_document")
    def test_get_document_detail(self, mock_get_doc, client):
        mock_get_doc.return_value = {
            "_id": "abc123",
            "filename": "report.pdf",
            "summary": "A financial report",
        }
        resp = client.get("/api/download/mongodb/document/abc123")
        assert resp.status_code == 200
        assert resp.json()["filename"] == "report.pdf"

    @patch.object(mongodb_service, "get_document")
    def test_get_document_detail_not_found(self, mock_get_doc, client):
        mock_get_doc.return_value = None
        resp = client.get("/api/download/mongodb/document/nonexistent")
        assert resp.status_code == 404

    @patch.object(mongodb_service, "delete_document")
    def test_delete_document_api(self, mock_delete, client):
        mock_delete.return_value = True
        resp = client.delete("/api/download/mongodb/document/abc123")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

    @patch.object(mongodb_service, "delete_document")
    def test_delete_document_not_found(self, mock_delete, client):
        mock_delete.return_value = False
        resp = client.delete("/api/download/mongodb/document/abc123")
        assert resp.status_code == 404


class TestScanSummarization:
    @patch.object(mongodb_service, "get_scan_summary_stats")
    def test_scan_summary_stats(self, mock_stats, client):
        mock_stats.return_value = {"pending": 10, "complete": 5, "failed": 1, "skipped": 0}
        resp = client.get("/api/download/mongodb/scan/summary-stats?scan_url=https://example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending"] == 10
        assert data["complete"] == 5
        mock_stats.assert_called_once_with("https://example.com")

    @patch.object(mongodb_service, "get_scan_summary_stats")
    def test_scan_summary_stats_error(self, mock_stats, client):
        mock_stats.side_effect = Exception("no connection")
        resp = client.get("/api/download/mongodb/scan/summary-stats?scan_url=https://example.com")
        assert resp.status_code == 200
        assert resp.json()["pending"] == 0

    @patch("app.api.routes.downloads.ai_summarization_service")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_summarize_scan_starts(self, mock_settings, mock_ai, client):
        mock_settings.ai_api_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "gpt-4o-mini"
        mock_ai.is_running.return_value = False
        mock_ai.summarize_pending_documents = AsyncMock()

        resp = client.post("/api/download/mongodb/scan/summarize", json={
            "scan_url": "https://example.com",
            "recreate": False,
        })
        assert resp.status_code == 200
        assert resp.json()["started"] is True

    @patch("app.api.routes.downloads.ai_summarization_service")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_summarize_scan_not_configured(self, mock_settings, mock_ai, client):
        mock_settings.ai_api_url = ""
        mock_settings.ai_api_key = ""
        mock_settings.ai_model = ""

        resp = client.post("/api/download/mongodb/scan/summarize", json={
            "scan_url": "https://example.com",
        })
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]

    @patch.object(mongodb_service, "reset_scan_summaries")
    @patch("app.api.routes.downloads.ai_summarization_service")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_summarize_scan_recreate(self, mock_settings, mock_ai, mock_reset, client):
        mock_settings.ai_api_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "gpt-4o-mini"
        mock_ai.is_running.return_value = False
        mock_ai.summarize_pending_documents = AsyncMock()
        mock_reset.return_value = 15

        resp = client.post("/api/download/mongodb/scan/summarize", json={
            "scan_url": "https://example.com",
            "recreate": True,
        })
        assert resp.status_code == 200
        assert resp.json()["started"] is True
        mock_reset.assert_called_once_with("https://example.com")

    def test_summarize_scan_no_url(self, client):
        resp = client.post("/api/download/mongodb/scan/summarize", json={})
        assert resp.status_code == 400


class TestRetryFailedScan:
    @patch("app.api.routes.downloads.ai_summarization_service")
    @patch.object(mongodb_service, "reset_scan_failed_summaries")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_retry_resets_and_starts(self, mock_settings, mock_reset, mock_ai, client):
        mock_settings.ai_api_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "gpt-4o-mini"
        mock_reset.return_value = 5
        mock_ai.is_running.return_value = False
        mock_ai.summarize_pending_documents = AsyncMock()

        resp = client.post("/api/download/mongodb/scan/retry-failed", json={
            "scan_url": "https://example.com",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["reset_count"] == 5
        assert data["started"] is True
        mock_reset.assert_called_once_with("https://example.com")

    @patch.object(mongodb_service, "reset_scan_failed_summaries")
    @patch("app.api.routes.downloads.runtime_settings")
    def test_retry_no_failures(self, mock_settings, mock_reset, client):
        mock_settings.ai_api_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.ai_api_key = "sk-test"
        mock_settings.ai_model = "gpt-4o-mini"
        mock_reset.return_value = 0

        resp = client.post("/api/download/mongodb/scan/retry-failed", json={
            "scan_url": "https://example.com",
        })
        assert resp.status_code == 200
        assert resp.json()["reset_count"] == 0
        assert resp.json()["started"] is False

    @patch("app.api.routes.downloads.runtime_settings")
    def test_retry_not_configured(self, mock_settings, client):
        mock_settings.ai_api_url = ""
        mock_settings.ai_api_key = ""
        mock_settings.ai_model = ""

        resp = client.post("/api/download/mongodb/scan/retry-failed", json={
            "scan_url": "https://example.com",
        })
        assert resp.status_code == 400

    def test_retry_no_url(self, client):
        resp = client.post("/api/download/mongodb/scan/retry-failed", json={})
        assert resp.status_code == 400

    @patch.object(mongodb_service, "get_scan_failed_summary_errors")
    def test_failed_details(self, mock_errors, client):
        mock_errors.return_value = [
            {"_id": "abc", "filename": "bad.pdf", "summary_error": "HTTP 500", "extension": ".pdf"},
            {"_id": "def", "filename": "worse.pdf", "summary_error": "Timeout", "extension": ".pdf"},
        ]
        resp = client.get("/api/download/mongodb/scan/failed-details?scan_url=https://example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["failures"][0]["summary_error"] == "HTTP 500"

    def test_failed_details_no_url(self, client):
        resp = client.get("/api/download/mongodb/scan/failed-details")
        assert resp.status_code == 400


class TestCSVExport:
    @patch.object(mongodb_service, "get_documents_for_export")
    def test_export_csv_success(self, mock_export, client):
        mock_export.return_value = [
            {
                "_id": "abc123",
                "filename": "report.pdf",
                "title": "Quarterly Financial Report",
                "short_summary": "A Q4 financial report.",
                "source_url": "https://example.com/report.pdf",
                "file_type_label": "PDF Document",
                "version_label": "v1",
                "extension": ".pdf",
                "category": "Financial Reports",
            },
            {
                "_id": "def456",
                "filename": "data.xlsx",
                "title": None,
                "short_summary": None,
                "source_url": "https://example.com/data.xlsx",
                "file_type_label": "Excel Spreadsheet",
                "version_label": "v2",
                "extension": ".xlsx",
                "category": None,  # uncategorized — must produce an empty cell, not crash
            },
        ]

        resp = client.get("/api/download/mongodb/export/csv?scan_url=https://example.com")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "document_export.csv" in resp.headers["content-disposition"]

        lines = resp.text.strip().split("\n")
        assert len(lines) == 3  # header + 2 data rows
        assert "Document Title" in lines[0]
        assert "Category" in lines[0]  # regression guard: M6 added category to the export
        # Rows sorted alphabetically by title (fallback to filename)
        # "data.xlsx" (no title, uses filename) sorts before "Quarterly Financial Report"
        assert "data.xlsx" in lines[1]
        assert "Quarterly Financial Report" in lines[2]
        # Categorized doc's category appears in its row; uncategorized produces an empty cell
        assert "Financial Reports" in lines[2]

    def test_export_csv_no_scan_url(self, client):
        resp = client.get("/api/download/mongodb/export/csv")
        assert resp.status_code == 400

    @patch.object(mongodb_service, "get_documents_for_export")
    def test_export_csv_empty(self, mock_export, client):
        mock_export.return_value = []
        resp = client.get("/api/download/mongodb/export/csv?scan_url=https://example.com")
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1  # header only

    @patch.object(mongodb_service, "get_documents_for_export")
    def test_export_csv_error(self, mock_export, client):
        mock_export.side_effect = Exception("DB error")
        resp = client.get("/api/download/mongodb/export/csv?scan_url=https://example.com")
        assert resp.status_code == 500


class TestDocumentsPage:
    def test_documents_page_loads(self, client):
        resp = client.get("/documents")
        assert resp.status_code == 200
        assert b"Search Documents" in resp.content


# ---------------------------------------------------------------------------
# Categorization helpers (M2). All mocked — see
# tests/integration/test_categories_roundtrip.py for the live round-trip.
# ---------------------------------------------------------------------------


def _mock_categories_setup(category_set_doc=None):
    """Wire mongodb_service._categories_collection + _get_collection mocks.

    Returns (categories_col_mock, docs_col_mock, db_mock) so the test can set
    up further expectations and assert calls.
    """
    docs_col = MagicMock()
    categories_col = MagicMock()
    categories_col.find_one.return_value = category_set_doc

    db = MagicMock()
    def _getitem(name):
        return {"documents": docs_col, "categories": categories_col}[name]
    db.__getitem__.side_effect = _getitem
    return categories_col, docs_col, db


class TestCategoryCRUD:

    def test_get_category_set_missing(self):
        cats_col, _docs, db = _mock_categories_setup(category_set_doc=None)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_ensure_indexes"):
            assert mongodb_service.get_category_set("https://x.test/scan") is None

    def test_get_category_set_found(self):
        cset = {
            "_id": ObjectId(),
            "scan_url": "https://x.test/scan",
            "categories": [{"name": "A", "description": "", "count": 3}],
        }
        cats_col, _docs, db = _mock_categories_setup(category_set_doc=cset)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_ensure_indexes"):
            result = mongodb_service.get_category_set("https://x.test/scan")
        assert result["scan_url"] == "https://x.test/scan"
        assert isinstance(result["_id"], str)  # stringified for JSON serializability
        assert result["categories"][0]["name"] == "A"

    def test_save_category_set_inserts_on_first_save(self):
        cats_col, _docs, db = _mock_categories_setup()
        oid = ObjectId()
        cats_col.replace_one.return_value = MagicMock(upserted_id=oid)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_ensure_indexes"):
            result_id = mongodb_service.save_category_set(
                scan_url="https://x.test/scan",
                categories=[{"name": "A", "description": "", "count": 0}],
                iterations_used=2,
                model="m",
                doc_count_at_creation=10,
            )
        assert result_id == str(oid)
        assert cats_col.replace_one.call_args[1]["upsert"] is True

    def test_save_category_set_replaces_existing(self):
        existing_id = ObjectId()
        cats_col, _docs, db = _mock_categories_setup()
        cats_col.replace_one.return_value = MagicMock(upserted_id=None)
        cats_col.find_one.return_value = {"_id": existing_id}
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_ensure_indexes"):
            result_id = mongodb_service.save_category_set(
                scan_url="https://x.test/scan",
                categories=[],
                iterations_used=1,
                model="m",
                doc_count_at_creation=0,
            )
        assert result_id == str(existing_id)

    def test_delete_category_set_clears_documents_too(self):
        cats_col, docs_col, db = _mock_categories_setup()
        cats_col.delete_one.return_value = MagicMock(deleted_count=1)
        docs_col.update_many.return_value = MagicMock(modified_count=5)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            removed = mongodb_service.delete_category_set("https://x.test/scan")
        assert removed is True
        docs_col.update_many.assert_called_once()

    def test_delete_category_set_no_set_returns_false(self):
        cats_col, docs_col, db = _mock_categories_setup()
        cats_col.delete_one.return_value = MagicMock(deleted_count=0)
        docs_col.update_many.return_value = MagicMock(modified_count=0)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            assert mongodb_service.delete_category_set("https://x.test/scan") is False

    def test_bulk_set_document_categories_empty_noop(self):
        with patch.object(mongodb_service, "_get_collection") as get_col:
            mongodb_service.bulk_set_document_categories({})
            get_col.assert_not_called()

    def test_bulk_set_document_categories_uses_bulk_write(self):
        docs_col = MagicMock()
        docs_col.bulk_write.return_value = MagicMock(modified_count=3)
        assignments = {str(ObjectId()): "A", str(ObjectId()): "B", str(ObjectId()): "A"}
        with patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            n = mongodb_service.bulk_set_document_categories(assignments)
        assert n == 3
        # One UpdateOne per document
        ops = docs_col.bulk_write.call_args[0][0]
        assert len(ops) == 3

    def test_get_category_counts_buckets_uncategorized_under_empty_key(self):
        docs_col = MagicMock()
        docs_col.aggregate.return_value = iter([
            {"_id": "Financial", "count": 12},
            {"_id": "Marketing", "count": 4},
            {"_id": "", "count": 7},  # uncategorized
        ])
        with patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            counts = mongodb_service.get_category_counts("https://x.test/scan")
        assert counts == {"Financial": 12, "Marketing": 4, "": 7}


class TestCategoryEditOperations:

    def _cset(self, categories):
        return {"_id": ObjectId(), "scan_url": "https://x.test/scan", "categories": categories}

    def test_rename_category_updates_set_and_docs(self):
        cset = self._cset([
            {"name": "Old", "description": "", "count": 5},
            {"name": "Other", "description": "", "count": 3},
        ])
        cats_col, docs_col, db = _mock_categories_setup(category_set_doc=cset)
        docs_col.update_many.return_value = MagicMock(modified_count=5)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            n = mongodb_service.rename_category("https://x.test/scan", "Old", "New")
        assert n == 5
        # Persisted set should now use the new name
        update_call = cats_col.update_one.call_args[0][1]["$set"]["categories"]
        names = [c["name"] for c in update_call]
        assert "New" in names and "Old" not in names

    def test_rename_category_rejects_collision(self):
        cset = self._cset([
            {"name": "A", "description": "", "count": 5},
            {"name": "B", "description": "", "count": 3},
        ])
        cats_col, _docs, db = _mock_categories_setup(category_set_doc=cset)
        with patch.object(mongodb_service, "_get_db", return_value=db):
            with pytest.raises(ValueError, match="already exists"):
                mongodb_service.rename_category("https://x.test/scan", "A", "B")

    def test_rename_category_case_insensitive_self_rename_allowed(self):
        cset = self._cset([{"name": "Reports", "description": "", "count": 5}])
        cats_col, docs_col, db = _mock_categories_setup(category_set_doc=cset)
        docs_col.update_many.return_value = MagicMock(modified_count=5)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            n = mongodb_service.rename_category("https://x.test/scan", "Reports", "reports")
        assert n == 5

    def test_rename_category_unknown_source_raises(self):
        cset = self._cset([{"name": "A", "description": "", "count": 1}])
        cats_col, _docs, db = _mock_categories_setup(category_set_doc=cset)
        with patch.object(mongodb_service, "_get_db", return_value=db):
            with pytest.raises(ValueError, match="not found"):
                mongodb_service.rename_category("https://x.test/scan", "Missing", "Whatever")

    def test_merge_categories_moves_docs_and_drops_source(self):
        cset = self._cset([
            {"name": "Source", "description": "", "count": 2},
            {"name": "Target", "description": "", "count": 10},
        ])
        cats_col, docs_col, db = _mock_categories_setup(category_set_doc=cset)
        docs_col.update_many.return_value = MagicMock(modified_count=2)
        docs_col.aggregate.return_value = iter([])  # for _refresh_category_set_counts
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            n = mongodb_service.merge_categories("https://x.test/scan", "Source", "Target")
        assert n == 2

    def test_merge_categories_same_name_is_noop(self):
        # No DB interactions because we early-return
        assert mongodb_service.merge_categories("https://x.test/scan", "A", "A") == 0

    def test_delete_category_nullifies_docs(self):
        cset = self._cset([
            {"name": "Doomed", "description": "", "count": 4},
            {"name": "Surviving", "description": "", "count": 9},
        ])
        cats_col, docs_col, db = _mock_categories_setup(category_set_doc=cset)
        docs_col.update_many.return_value = MagicMock(modified_count=4)
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col):
            n = mongodb_service.delete_category("https://x.test/scan", "Doomed")
        assert n == 4
        remaining = cats_col.update_one.call_args[0][1]["$set"]["categories"]
        assert [c["name"] for c in remaining] == ["Surviving"]


class TestDeleteScanEndpoint:
    """Tests for DELETE /api/download/mongodb/scan."""

    @pytest.fixture(autouse=True)
    def _clean_session_stores(self):
        # Ensure session dicts are empty so the active-session check doesn't
        # leak state between tests.
        from app.services.session_store import (
            scrape_sessions, download_sessions, categorize_sessions,
        )
        scrape_sessions.clear()
        download_sessions.clear()
        categorize_sessions.clear()
        yield
        scrape_sessions.clear()
        download_sessions.clear()
        categorize_sessions.clear()

    def test_missing_scan_url_returns_400(self, client):
        resp = client.delete("/api/download/mongodb/scan?scan_url=")
        assert resp.status_code == 400
        assert "scan_url" in resp.json()["detail"]

    @patch.object(mongodb_service, "delete_scan")
    @patch("app.api.routes.downloads.history_service.delete_scan_by_url")
    def test_happy_path_cascades_and_returns_counts(self, mock_hist, mock_mongo, client):
        mock_mongo.return_value = {
            "documents": 17, "gridfs_files": 17, "gridfs_failed": 0,
            "category_set_deleted": 1,
        }
        mock_hist.return_value = 2
        resp = client.delete("/api/download/mongodb/scan?scan_url=https://x.test/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["documents_deleted"] == 17
        assert body["gridfs_files_deleted"] == 17
        assert body["category_set_deleted"] is True
        assert body["history_rows_deleted"] == 2
        mock_mongo.assert_called_once_with("https://x.test/scan")
        mock_hist.assert_called_once_with("https://x.test/scan")

    def test_refuses_409_with_active_scrape_session(self, client):
        from app.services.session_store import scrape_sessions
        request = MagicMock()
        request.url = "https://x.test/scan"
        scrape_sessions["s1"] = {"request": request, "status": "scanning"}
        resp = client.delete("/api/download/mongodb/scan?scan_url=https://x.test/scan")
        assert resp.status_code == 409
        assert "scrape" in resp.json()["detail"]

    def test_refuses_409_with_active_categorize_session(self, client):
        from app.services.session_store import categorize_sessions
        import asyncio
        categorize_sessions["c1"] = {
            "scan_url": "https://x.test/scan", "status": "running",
            "queue": asyncio.Queue(), "result": None, "error": None, "task": None,
        }
        resp = client.delete("/api/download/mongodb/scan?scan_url=https://x.test/scan")
        assert resp.status_code == 409
        assert "categorize" in resp.json()["detail"]

    @patch.object(mongodb_service, "delete_scan")
    def test_ignores_completed_sessions(self, mock_mongo, client):
        """Sessions whose status is complete/cancelled/error don't block deletion."""
        from app.services.session_store import categorize_sessions
        import asyncio
        categorize_sessions["done"] = {
            "scan_url": "https://x.test/scan", "status": "complete",
            "queue": asyncio.Queue(), "result": None, "error": None, "task": None,
        }
        mock_mongo.return_value = {
            "documents": 1, "gridfs_files": 1, "gridfs_failed": 0,
            "category_set_deleted": 0,
        }
        with patch("app.api.routes.downloads.history_service.delete_scan_by_url",
                   return_value=0):
            resp = client.delete("/api/download/mongodb/scan?scan_url=https://x.test/scan")
        assert resp.status_code == 200


class TestDeleteScanService:
    """Tests for mongodb_service.delete_scan (mocked Mongo)."""

    def test_returns_counts_for_clean_delete(self):
        docs = [
            {"_id": ObjectId(), "gridfs_id": ObjectId(), "filename": "a.pdf"},
            {"_id": ObjectId(), "gridfs_id": ObjectId(), "filename": "b.pdf"},
        ]
        docs_col = MagicMock()
        docs_col.find.return_value = iter(docs)
        docs_col.delete_many.return_value = MagicMock(deleted_count=2)
        cats_col = MagicMock()
        cats_col.delete_one.return_value = MagicMock(deleted_count=1)
        db = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            "documents": docs_col, "categories": cats_col,
        }[name]
        fs = MagicMock()

        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col), \
             patch("gridfs.GridFS", return_value=fs):
            result = mongodb_service.delete_scan("https://x.test/scan")

        assert result["documents"] == 2
        assert result["gridfs_files"] == 2
        assert result["gridfs_failed"] == 0
        assert result["category_set_deleted"] == 1
        assert fs.delete.call_count == 2

    def test_tolerates_missing_gridfs_file(self):
        import gridfs
        docs = [{"_id": ObjectId(), "gridfs_id": ObjectId(), "filename": "ghost.pdf"}]
        docs_col = MagicMock()
        docs_col.find.return_value = iter(docs)
        docs_col.delete_many.return_value = MagicMock(deleted_count=1)
        cats_col = MagicMock()
        cats_col.delete_one.return_value = MagicMock(deleted_count=0)
        db = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            "documents": docs_col, "categories": cats_col,
        }[name]
        fs = MagicMock()
        fs.delete.side_effect = gridfs.NoFile()

        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col), \
             patch("gridfs.GridFS", return_value=fs):
            result = mongodb_service.delete_scan("https://x.test/scan")

        # NoFile is counted as deleted (the file is already gone, which is the desired end state).
        assert result["gridfs_files"] == 1
        assert result["gridfs_failed"] == 0

    def test_empty_scan_returns_zeros(self):
        docs_col = MagicMock()
        docs_col.find.return_value = iter([])
        cats_col = MagicMock()
        cats_col.delete_one.return_value = MagicMock(deleted_count=0)
        db = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            "documents": docs_col, "categories": cats_col,
        }[name]
        fs = MagicMock()
        with patch.object(mongodb_service, "_get_db", return_value=db), \
             patch.object(mongodb_service, "_get_collection", return_value=docs_col), \
             patch("gridfs.GridFS", return_value=fs):
            result = mongodb_service.delete_scan("https://nonexistent.test/scan")
        assert result == {
            "documents": 0, "gridfs_files": 0, "gridfs_failed": 0, "category_set_deleted": 0,
        }
        docs_col.delete_many.assert_not_called()
