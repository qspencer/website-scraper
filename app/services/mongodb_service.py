"""Service for storing and retrieving documents in MongoDB."""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)

# Lazy-loaded client
_client = None
_current_uri = None


def _get_client():
    """Get or create a pymongo MongoClient, reconnecting if URI changed."""
    global _client, _current_uri
    from pymongo import MongoClient

    uri = runtime_settings.mongodb_uri
    if _client is None or uri != _current_uri:
        if _client is not None:
            _client.close()
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _current_uri = uri
        logger.info(f"MongoDB client created for {uri}")
    return _client


def _get_db():
    """Get the configured database."""
    client = _get_client()
    return client[runtime_settings.mongodb_database]


def _get_collection():
    """Get the documents collection."""
    return _get_db()["documents"]


def _ensure_indexes():
    """Create indexes for search and queries."""
    col = _get_collection()
    col.create_index([("filename", "text"), ("extracted_text", "text")],
                     name="text_search", default_language="english")
    col.create_index("source_url")
    col.create_index("scan_url")
    col.create_index("downloaded_at")
    col.create_index("extension")
    logger.debug("MongoDB indexes ensured")


def _diagnose_connection_error(e: Exception) -> str:
    """Return a user-friendly diagnostic message for a MongoDB connection error."""
    err = str(e).lower()

    if "connection refused" in err or "errno 111" in err:
        return (
            "MongoDB is not running. "
            "Please install and start MongoDB, then try again."
        )
    if "no such host" in err or "nodename nor servname" in err or "name or service not known" in err:
        return (
            "Could not reach the MongoDB server. "
            "Please check the connection URI in Settings."
        )
    if "authentication failed" in err or "auth" in err:
        return (
            "MongoDB authentication failed. "
            "Please check your username and password in the connection URI."
        )
    if "timed out" in err or "timeout" in err:
        return (
            "Connection to MongoDB timed out. "
            "The server may be slow or unreachable. Check the URI and network."
        )
    if "ssl" in err or "tls" in err or "certificate" in err:
        return (
            "SSL/TLS error connecting to MongoDB. "
            "Check your connection URI and certificate configuration."
        )
    # Generic fallback — include the raw error truncated
    return f"Connection failed: {str(e)[:120]}"


def test_connection() -> Dict[str, Any]:
    """Test the MongoDB connection. Returns status dict."""
    try:
        client = _get_client()
        # ping triggers actual connection
        client.admin.command("ping")
        db = _get_db()
        doc_count = db["documents"].count_documents({})
        return {
            "connected": True,
            "message": f"Connected ({doc_count} documents stored)",
            "document_count": doc_count,
            "help_url": None,
        }
    except Exception as e:
        message = _diagnose_connection_error(e)
        return {
            "connected": False,
            "message": message,
            "document_count": 0,
            "help_url": "https://www.mongodb.com/docs/manual/installation/",
        }


def store_document(
    file_data: bytes,
    filename: str,
    extension: str,
    source_url: str,
    source_page: str,
    scan_url: str,
    file_size_bytes: Optional[int] = None,
    content_type: Optional[str] = None,
    extracted_text: Optional[str] = None,
    text_extraction_status: str = "pending",
) -> str:
    """
    Store a document in MongoDB using GridFS.

    Returns the string ID of the created document record.
    """
    import gridfs

    db = _get_db()
    fs = gridfs.GridFS(db)

    # Store file in GridFS
    gridfs_id = fs.put(
        file_data,
        filename=filename,
        content_type=content_type or "application/octet-stream",
    )

    # Store metadata in documents collection
    doc = {
        "filename": filename,
        "extension": extension,
        "source_url": source_url,
        "source_page": source_page,
        "scan_url": scan_url,
        "file_size_bytes": file_size_bytes or len(file_data),
        "content_type": content_type,
        "gridfs_id": gridfs_id,
        "downloaded_at": datetime.now(timezone.utc),
        "extracted_text": extracted_text,
        "text_extraction_status": text_extraction_status,
        "summary": None,
        "keywords": [],
        "document_type": None,
        "summary_status": "pending",
        "summarized_at": None,
        "summary_model": None,
    }

    col = _get_collection()
    result = col.insert_one(doc)
    _ensure_indexes()

    logger.info(f"Stored document: {filename} (id={result.inserted_id})")
    return str(result.inserted_id)


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    """Get document metadata by ID."""
    from bson import ObjectId

    col = _get_collection()
    doc = col.find_one({"_id": ObjectId(doc_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
        doc["gridfs_id"] = str(doc["gridfs_id"])
    return doc


def get_document_file(doc_id: str) -> Optional[bytes]:
    """Get the actual file bytes for a document."""
    import gridfs
    from bson import ObjectId

    col = _get_collection()
    doc = col.find_one({"_id": ObjectId(doc_id)})
    if not doc:
        return None

    db = _get_db()
    fs = gridfs.GridFS(db)
    try:
        grid_out = fs.get(doc["gridfs_id"])
        return grid_out.read()
    except gridfs.NoFile:
        logger.error(f"GridFS file not found for document {doc_id}")
        return None


def search_documents(
    query: str = "",
    scan_url: Optional[str] = None,
    extension: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search documents by text query and/or filters."""
    col = _get_collection()

    filter_dict: Dict[str, Any] = {}

    if query:
        filter_dict["$text"] = {"$search": query}

    if scan_url:
        filter_dict["scan_url"] = scan_url

    if extension:
        filter_dict["extension"] = extension

    # If text search, sort by relevance; otherwise by date
    if query:
        cursor = col.find(
            filter_dict,
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
    else:
        cursor = col.find(filter_dict).sort("downloaded_at", -1).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        doc["gridfs_id"] = str(doc["gridfs_id"])
        # Don't send full extracted text in search results
        if "extracted_text" in doc:
            text = doc["extracted_text"]
            doc["extracted_text_preview"] = text[:300] + "..." if text and len(text) > 300 else text
            del doc["extracted_text"]
        results.append(doc)

    return results


def get_document_count() -> int:
    """Get total number of stored documents."""
    try:
        col = _get_collection()
        return col.count_documents({})
    except Exception:
        return 0


def get_pending_summaries(limit: int = 50) -> List[Dict[str, Any]]:
    """Get documents that need summarization."""
    col = _get_collection()
    cursor = col.find(
        {
            "summary_status": "pending",
            "text_extraction_status": "complete",
            "extracted_text": {"$nin": [None, ""]},
        },
        {"_id": 1, "filename": 1, "extracted_text": 1, "extension": 1},
    ).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


def update_summary(
    doc_id: str,
    summary: str,
    keywords: List[str],
    document_type: str,
    model: str,
) -> bool:
    """Update a document with its AI-generated summary."""
    from bson import ObjectId

    col = _get_collection()
    result = col.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {
            "summary": summary,
            "keywords": keywords,
            "document_type": document_type,
            "summary_status": "complete",
            "summarized_at": datetime.now(timezone.utc),
            "summary_model": model,
        }},
    )
    return result.modified_count > 0


def mark_summary_failed(doc_id: str) -> bool:
    """Mark a document's summarization as failed."""
    from bson import ObjectId

    col = _get_collection()
    result = col.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {"summary_status": "failed"}},
    )
    return result.modified_count > 0


def reset_failed_summaries() -> int:
    """Reset all failed summaries back to pending so they can be retried."""
    col = _get_collection()
    result = col.update_many(
        {"summary_status": "failed"},
        {"$set": {"summary_status": "pending"}},
    )
    return result.modified_count


def get_summary_stats() -> Dict[str, int]:
    """Get counts of documents by summary status."""
    col = _get_collection()
    pipeline = [
        {"$group": {"_id": "$summary_status", "count": {"$sum": 1}}},
    ]
    stats = {"pending": 0, "complete": 0, "failed": 0, "skipped": 0}
    for row in col.aggregate(pipeline):
        status = row["_id"]
        if status in stats:
            stats[status] = row["count"]
    return stats


def delete_document(doc_id: str) -> bool:
    """Delete a document and its GridFS file."""
    import gridfs
    from bson import ObjectId

    col = _get_collection()
    doc = col.find_one({"_id": ObjectId(doc_id)})
    if not doc:
        return False

    # Delete GridFS file
    db = _get_db()
    fs = gridfs.GridFS(db)
    try:
        fs.delete(doc["gridfs_id"])
    except Exception as e:
        logger.warning(f"Failed to delete GridFS file for {doc_id}: {e}")

    # Delete metadata
    col.delete_one({"_id": ObjectId(doc_id)})
    logger.info(f"Deleted document: {doc_id}")
    return True
