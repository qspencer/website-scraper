"""Service for storing and retrieving documents in MongoDB."""

import hashlib
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings
from app.utils.document_types import get_document_type_label

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


_indexes_ensured: bool = False


def _ensure_indexes(force: bool = False):
    """Create indexes for search and queries (idempotent; runs at most once per process).

    Previously called on every store_document insert, which probed index_information
    per write. Pass force=True to re-run (e.g. after dropping the collection externally).
    """
    global _indexes_ensured
    if _indexes_ensured and not force:
        return
    col = _get_collection()
    # Drop old text index if it exists with different fields
    try:
        existing = col.index_information()
        if "text_search" in existing:
            weights = existing["text_search"].get("weights", {})
            if "title" not in weights or "short_summary" not in weights:
                col.drop_index("text_search")
                logger.info("Dropped old text_search index to rebuild with new fields")
    except Exception:
        pass
    col.create_index(
        [("filename", "text"), ("extracted_text", "text"),
         ("summary", "text"), ("short_summary", "text"),
         ("title", "text"), ("keywords", "text")],
        name="text_search", default_language="english",
    )
    _indexes_ensured = True
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
    text_extraction_method: Optional[str] = None,
    text_extraction_error: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Store a document in MongoDB using GridFS.

    If a document with the same source_url and scan_url already exists,
    it is replaced (file data and metadata are updated).

    Returns a tuple of (doc_id, action) where action is one of:
    "new", "updated", or "unchanged".
    """
    import gridfs

    db = _get_db()
    fs = gridfs.GridFS(db)
    col = _get_collection()

    # Compute content hash and file type label
    content_hash = hashlib.sha256(file_data).hexdigest()
    file_type_label = get_document_type_label(extension)

    # Check for existing document with the same source URL in this scan
    existing = col.find_one({"source_url": source_url, "scan_url": scan_url})

    if existing:
        old_hash = existing.get("content_hash")
        content_changed = old_hash != content_hash

        # Content unchanged — skip re-upload entirely
        if not content_changed:
            logger.info(f"Document unchanged, skipping: {filename} (id={existing['_id']})")
            return str(existing["_id"]), "unchanged"

        # Content changed — replace file and bump version
        version = existing.get("version", 0) + 1

        # Delete the old GridFS file
        try:
            fs.delete(existing["gridfs_id"])
        except Exception as e:
            logger.warning(f"Failed to delete old GridFS file for {filename}: {e}")

        # Store new file in GridFS
        gridfs_id = fs.put(
            file_data,
            filename=filename,
            content_type=content_type or "application/octet-stream",
        )

        # Update the existing record
        update_fields = {
            "filename": filename,
            "extension": extension,
            "file_type_label": file_type_label,
            "source_page": source_page,
            "file_size_bytes": file_size_bytes or len(file_data),
            "content_type": content_type,
            "gridfs_id": gridfs_id,
            "downloaded_at": datetime.now(timezone.utc),
            "extracted_text": extracted_text,
            "text_extraction_status": text_extraction_status,
            "text_extraction_method": text_extraction_method,
            "text_extraction_error": text_extraction_error,
            "content_hash": content_hash,
            "version": version,
            "version_label": f"v{version}",
            "title": None,
            "short_summary": None,
            "summary": None,
            "keywords": [],
            "document_type": None,
            "summary_status": "pending",
            "summarized_at": None,
            "summary_model": None,
        }

        col.update_one({"_id": existing["_id"]}, {"$set": update_fields})

        logger.info(f"Updated existing document: {filename} (id={existing['_id']}, v{version})")
        return str(existing["_id"]), "updated"

    # Store new file in GridFS
    gridfs_id = fs.put(
        file_data,
        filename=filename,
        content_type=content_type or "application/octet-stream",
    )

    # Store metadata in documents collection
    doc = {
        "filename": filename,
        "extension": extension,
        "file_type_label": file_type_label,
        "source_url": source_url,
        "source_page": source_page,
        "scan_url": scan_url,
        "file_size_bytes": file_size_bytes or len(file_data),
        "content_type": content_type,
        "gridfs_id": gridfs_id,
        "downloaded_at": datetime.now(timezone.utc),
        "extracted_text": extracted_text,
        "text_extraction_status": text_extraction_status,
        "text_extraction_method": text_extraction_method,
        "content_hash": content_hash,
        "version": 1,
        "version_label": "v1",
        "title": None,
        "short_summary": None,
        "summary": None,
        "keywords": [],
        "document_type": None,
        "summary_status": "pending",
        "summarized_at": None,
        "summary_model": None,
    }

    result = col.insert_one(doc)
    _ensure_indexes()

    logger.info(f"Stored document: {filename} (id={result.inserted_id})")
    return str(result.inserted_id), "new"


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
    title: str = "",
    short_summary: str = "",
) -> bool:
    """Update a document with its AI-generated summary."""
    from bson import ObjectId

    col = _get_collection()
    result = col.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {
            "title": title,
            "short_summary": short_summary,
            "summary": summary,
            "keywords": keywords,
            "document_type": document_type,
            "summary_status": "complete",
            "summary_error": None,
            "summarized_at": datetime.now(timezone.utc),
            "summary_model": model,
        }},
    )
    return result.modified_count > 0


def retry_text_extraction(progress_callback=None) -> Dict[str, int]:
    """Re-attempt text extraction for documents where it previously failed.

    First tries the standard extractor (handles newly added formats like .xls).
    For PDFs that still fail, attempts Stirling PDF (fast text extract, then OCR).
    Returns a dict with counts: total, succeeded, stirling_fast, stirling_ocr, failed.

    Args:
        progress_callback: Optional callable(current, total, filename) called after each doc.
    """
    import time
    import gridfs
    from app.services.text_extraction_service import extract_text

    col = _get_collection()
    db = _get_db()
    fs = gridfs.GridFS(db)

    docs = list(col.find(
        {
            "summary_status": {"$in": ["pending", "failed"]},
            "text_extraction_status": {"$in": ["failed", "unsupported"]},
        },
        {"_id": 1, "filename": 1, "extension": 1, "gridfs_id": 1, "file_size_bytes": 1},
    ))

    stats = {"total": len(docs), "succeeded": 0, "stirling_fast": 0, "stirling_ocr": 0, "failed": 0}

    if not docs:
        return stats

    # Check if Stirling PDF is available for OCR fallback
    stirling_available = False
    try:
        from app.services import stirling_pdf_service
        stirling_available = stirling_pdf_service.is_configured()
        if stirling_available:
            logger.info("Stirling PDF available for OCR fallback")
    except Exception:
        pass

    pdf_count = sum(1 for d in docs if d["extension"].lower() == ".pdf")
    non_pdf_count = stats["total"] - pdf_count
    logger.info(
        f"Retrying text extraction for {stats['total']} documents "
        f"({pdf_count} PDFs, {non_pdf_count} other)"
        f"{' — Stirling PDF available for OCR' if stirling_available else ''}"
    )

    # Pre-compute remaining bytes for ETA estimation
    doc_sizes = [d.get("file_size_bytes") or 0 for d in docs]
    total_bytes = sum(doc_sizes)
    bytes_processed = 0
    start_time = time.monotonic()

    def _report_progress(i, filename, file_size, method_label):
        """Send rich progress info through the callback."""
        if not progress_callback:
            return
        elapsed = time.monotonic() - start_time
        remaining_bytes = total_bytes - bytes_processed
        bytes_per_sec = bytes_processed / elapsed if elapsed > 1 else 0
        eta_seconds = remaining_bytes / bytes_per_sec if bytes_per_sec > 0 else 0
        progress_callback({
            "current": i + 1,
            "total": stats["total"],
            "current_file": filename,
            "current_file_size": file_size,
            "method": method_label,
            "elapsed_seconds": round(elapsed),
            "bytes_processed": bytes_processed,
            "bytes_remaining": remaining_bytes,
            "bytes_per_second": round(bytes_per_sec),
            "eta_seconds": round(eta_seconds),
        })

    for i, doc in enumerate(docs):
        filename = doc["filename"]
        ext = doc["extension"].lower()
        file_size = doc_sizes[i]

        _report_progress(i, filename, file_size, "starting")

        try:
            grid_out = fs.get(doc["gridfs_id"])
            file_data = grid_out.read()
        except Exception:
            stats["failed"] += 1
            bytes_processed += file_size
            continue

        # Try standard extraction first
        method = None
        extraction_error = ""
        try:
            text, status, extraction_error = extract_text(file_data, ext)
            if status == "complete":
                method = "pypdf2" if ext == ".pdf" else "standard"
        except Exception as e:
            logger.warning(f"Standard extraction error for {filename}: {e}")
            text, status, extraction_error = "", "failed", str(e)

        # If standard extraction failed for PDFs, try Stirling PDF
        if status != "complete" and ext == ".pdf" and stirling_available:
            # Fast path first: direct text extraction (~1 second)
            _report_progress(i, filename, file_size, "stirling_fast")
            fast_text = stirling_pdf_service.extract_text_direct(file_data)
            if fast_text:
                text = fast_text
                status = "complete"
                method = "stirling_fast"
                stats["stirling_fast"] += 1
                logger.info(f"[{i+1}/{stats['total']}] Stirling fast extract: {filename}")
            else:
                # Slow path: OCR (1-5 minutes per file)
                _report_progress(i, filename, file_size, "stirling_ocr")
                remaining_ocr = sum(
                    1 for d in docs[i:]
                    if d["extension"].lower() == ".pdf"
                ) - 1
                logger.info(
                    f"[{i+1}/{stats['total']}] Stirling OCR for: {filename} "
                    f"(~2-4 min, {remaining_ocr} more PDFs after this)"
                )
                ocr_text = stirling_pdf_service.extract_text_via_ocr(file_data)
                if ocr_text:
                    text = ocr_text
                    status = "complete"
                    method = "stirling_ocr"
                    stats["stirling_ocr"] += 1

        bytes_processed += file_size

        if status == "complete" and text and text.strip():
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "extracted_text": text.strip(),
                    "text_extraction_status": "complete",
                    "text_extraction_method": method,
                    "text_extraction_error": None,
                    "summary_status": "pending",
                    "summary_error": None,
                }},
            )
            stats["succeeded"] += 1
            logger.info(f"[{i+1}/{stats['total']}] Re-extracted text for: {filename}")
        else:
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"text_extraction_error": extraction_error or "Extraction failed"}},
            )
            stats["failed"] += 1

    logger.info(
        f"Text re-extraction complete: {stats['succeeded']} succeeded "
        f"({stats['stirling_fast']} fast, {stats['stirling_ocr']} OCR), "
        f"{stats['failed']} failed out of {stats['total']}"
    )
    return stats


def mark_unsummarizable_documents() -> int:
    """Mark pending documents that can't be summarized due to failed text extraction."""
    col = _get_collection()
    result = col.update_many(
        {
            "summary_status": "pending",
            "$or": [
                {"text_extraction_status": {"$in": ["failed", "unsupported"]}},
                {"extracted_text": {"$in": [None, ""]}},
            ],
        },
        {"$set": {
            "summary_status": "failed",
            "summary_error": "Text extraction failed or unsupported file type",
        }},
    )
    return result.modified_count


def mark_summary_failed(doc_id: str, error: str = "") -> bool:
    """Mark a document's summarization as failed with an error reason."""
    from bson import ObjectId

    col = _get_collection()
    result = col.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {
            "summary_status": "failed",
            "summary_error": error,
        }},
    )
    return result.modified_count > 0


def reset_failed_summaries() -> int:
    """Reset all failed summaries back to pending so they can be retried."""
    col = _get_collection()
    result = col.update_many(
        {"summary_status": "failed"},
        {"$set": {"summary_status": "pending", "summary_error": None}},
    )
    return result.modified_count


def reset_scan_failed_summaries(scan_url: str) -> int:
    """Reset failed summaries for a specific scan back to pending."""
    col = _get_collection()
    result = col.update_many(
        {"scan_url": scan_url, "summary_status": "failed"},
        {"$set": {"summary_status": "pending", "summary_error": None}},
    )
    return result.modified_count


def get_scan_failed_summary_errors(scan_url: str) -> List[Dict[str, Any]]:
    """Get failed documents with their error details for a specific scan."""
    col = _get_collection()
    cursor = col.find(
        {"scan_url": scan_url, "summary_status": "failed"},
        {"filename": 1, "summary_error": 1, "extension": 1},
    ).sort("filename", 1)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


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


def get_scan_summary_stats(scan_url: str) -> Dict[str, int]:
    """Get counts of documents by summary status for a specific scan."""
    col = _get_collection()
    pipeline = [
        {"$match": {"scan_url": scan_url}},
        {"$group": {"_id": "$summary_status", "count": {"$sum": 1}}},
    ]
    stats = {"pending": 0, "complete": 0, "failed": 0, "skipped": 0}
    for row in col.aggregate(pipeline):
        status = row["_id"]
        if status in stats:
            stats[status] = row["count"]
    return stats


def get_extraction_stats() -> Dict[str, int]:
    """Get counts of PDF documents by text extraction method.

    Returns counts for each method (pypdf2, stirling_fast, stirling_ocr)
    plus failed/pending PDFs and total PDFs.
    """
    col = _get_collection()
    pipeline = [
        {"$match": {"extension": {"$regex": r"\.pdf$", "$options": "i"}}},
        {"$group": {"_id": "$text_extraction_method", "count": {"$sum": 1}}},
    ]
    stats = {
        "total_pdfs": 0,
        "pypdf2": 0,
        "stirling_fast": 0,
        "stirling_ocr": 0,
        "failed": 0,
    }
    for row in col.aggregate(pipeline):
        method = row["_id"]
        count = row["count"]
        stats["total_pdfs"] += count
        if method in stats:
            stats[method] = count
        else:
            # None or unrecognized method — count as failed/pending
            stats["failed"] += count
    return stats


def get_scan_extraction_stats(scan_url: str) -> Dict[str, int]:
    """Get counts of PDF documents by text extraction method for a specific scan."""
    col = _get_collection()
    pipeline = [
        {"$match": {"scan_url": scan_url, "extension": {"$regex": r"\.pdf$", "$options": "i"}}},
        {"$group": {"_id": "$text_extraction_method", "count": {"$sum": 1}}},
    ]
    stats = {
        "total_pdfs": 0,
        "pypdf2": 0,
        "stirling_fast": 0,
        "stirling_ocr": 0,
        "failed": 0,
    }
    for row in col.aggregate(pipeline):
        method = row["_id"]
        count = row["count"]
        stats["total_pdfs"] += count
        if method in stats:
            stats[method] = count
        else:
            stats["failed"] += count
    return stats


def reset_scan_summaries(scan_url: str) -> int:
    """Reset all summaries for a specific scan back to pending."""
    col = _get_collection()
    result = col.update_many(
        {"scan_url": scan_url, "summary_status": {"$in": ["complete", "failed"]}},
        {"$set": {
            "title": None,
            "short_summary": None,
            "summary": None,
            "keywords": [],
            "document_type": None,
            "summary_status": "pending",
            "summary_error": None,
            "summarized_at": None,
            "summary_model": None,
        }},
    )
    return result.modified_count


def get_documents_for_export(scan_url: str) -> List[Dict[str, Any]]:
    """Get all documents for a scan, projecting only fields needed for CSV export."""
    col = _get_collection()
    projection = {
        "filename": 1,
        "title": 1,
        "short_summary": 1,
        "source_url": 1,
        "file_type_label": 1,
        "version_label": 1,
        "content_hash": 1,
        "extension": 1,
    }
    cursor = col.find(
        {"scan_url": scan_url}, projection
    ).sort("filename", 1)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


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
