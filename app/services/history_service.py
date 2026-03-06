"""Service for managing scan history."""

from typing import List, Dict, Any
from datetime import datetime

from app.core import database as db
from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)


def save_scan(scan_data: Dict[str, Any]) -> None:
    """Save a completed scan to history and prune old entries."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scan_history (
                url, crawl_option, max_depth, scan_mode, document_filter,
                pages_scanned, documents_found, scan_error_count, document_error_count,
                duration_seconds, total_size_bytes,
                largest_file_name, largest_file_size,
                smallest_file_name, smallest_file_size,
                completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_data["url"],
            scan_data["crawl_option"],
            scan_data["max_depth"],
            scan_data["scan_mode"],
            scan_data["document_filter"],
            scan_data["pages_scanned"],
            scan_data["documents_found"],
            scan_data["scan_error_count"],
            scan_data["document_error_count"],
            scan_data.get("duration_seconds"),
            scan_data.get("total_size_bytes"),
            scan_data.get("largest_file_name"),
            scan_data.get("largest_file_size"),
            scan_data.get("smallest_file_name"),
            scan_data.get("smallest_file_size"),
            datetime.now().isoformat(),
        ))

        # Prune old entries
        limit = runtime_settings.scan_history_limit
        cursor.execute("""
            DELETE FROM scan_history
            WHERE id NOT IN (
                SELECT id FROM scan_history ORDER BY id DESC LIMIT ?
            )
        """, (limit,))

        pruned = cursor.rowcount
        if pruned > 0:
            logger.debug(f"Pruned {pruned} old scan history entries")

    logger.info(f"Scan history saved: {scan_data['url']} ({scan_data['documents_found']} docs)")


def get_history(limit: int = None) -> List[Dict[str, Any]]:
    """Get scan history, newest first."""
    if limit is None:
        limit = runtime_settings.scan_history_limit

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scan_history
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def clear_history() -> None:
    """Clear all scan history."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scan_history")
    logger.info("Scan history cleared")
