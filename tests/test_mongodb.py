"""Tests for MongoDB service and download endpoints."""

import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
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
        mock_col.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        mock_get_col.return_value = mock_col

        with patch("gridfs.GridFS", return_value=mock_fs):
            doc_id = mongodb_service.store_document(
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
