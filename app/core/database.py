"""SQLite database for persisting application data."""

import sqlite3
import json
import os
from typing import Any, Optional, Dict
from contextlib import contextmanager

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Database file location (in the app directory)
DB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(DB_DIR, "scraper.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_database():
    """Initialize the database schema."""
    logger.info(f"Initializing database at {DB_PATH}")

    with get_db() as conn:
        cursor = conn.cursor()

        # Settings table - key-value store for application settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Scan history table (for future use)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                documents_found INTEGER DEFAULT 0,
                pages_scanned INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)

        logger.info("Database initialized successfully")


def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()

        if row:
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return row["value"]

        return default


def set_setting(key: str, value: Any) -> None:
    """Set a setting value in the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        json_value = json.dumps(value)

        cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (key, json_value))

        logger.debug(f"Setting '{key}' saved to database")


def get_all_settings() -> Dict[str, Any]:
    """Get all settings from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()

        settings = {}
        for row in rows:
            try:
                settings[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                settings[row["key"]] = row["value"]

        return settings


def delete_setting(key: str) -> None:
    """Delete a setting from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM settings WHERE key = ?", (key,))


def clear_all_settings() -> None:
    """Clear all settings from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM settings")
        logger.info("All settings cleared from database")


# Initialize database on module import
init_database()
