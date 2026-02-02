"""Tests for the database module."""

import pytest
import os
import tempfile
from unittest.mock import patch

# We need to patch the DB_PATH before importing the database module
@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        temp_path = f.name

    # Patch the DB_PATH
    with patch('app.core.database.DB_PATH', temp_path):
        # Re-import to use the patched path
        from app.core import database as db
        db.DB_PATH = temp_path
        db.init_database()
        yield db

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


class TestDatabaseInit:
    def test_init_creates_tables(self, temp_db):
        """Test that init_database creates the required tables."""
        with temp_db.get_db() as conn:
            cursor = conn.cursor()

            # Check settings table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
            )
            assert cursor.fetchone() is not None

            # Check scan_history table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_history'"
            )
            assert cursor.fetchone() is not None


class TestSettingsCRUD:
    def test_set_and_get_setting(self, temp_db):
        """Test setting and getting a value."""
        temp_db.set_setting("test_key", "test_value")
        result = temp_db.get_setting("test_key")
        assert result == "test_value"

    def test_get_setting_default(self, temp_db):
        """Test getting a non-existent setting returns default."""
        result = temp_db.get_setting("nonexistent", default="default_value")
        assert result == "default_value"

    def test_get_setting_no_default(self, temp_db):
        """Test getting a non-existent setting returns None."""
        result = temp_db.get_setting("nonexistent")
        assert result is None

    def test_set_setting_integer(self, temp_db):
        """Test setting an integer value."""
        temp_db.set_setting("int_key", 42)
        result = temp_db.get_setting("int_key")
        assert result == 42
        assert isinstance(result, int)

    def test_set_setting_float(self, temp_db):
        """Test setting a float value."""
        temp_db.set_setting("float_key", 3.14)
        result = temp_db.get_setting("float_key")
        assert result == 3.14
        assert isinstance(result, float)

    def test_set_setting_boolean(self, temp_db):
        """Test setting a boolean value."""
        temp_db.set_setting("bool_key", True)
        result = temp_db.get_setting("bool_key")
        assert result is True

    def test_set_setting_list(self, temp_db):
        """Test setting a list value."""
        temp_db.set_setting("list_key", [1, 2, 3])
        result = temp_db.get_setting("list_key")
        assert result == [1, 2, 3]

    def test_set_setting_dict(self, temp_db):
        """Test setting a dict value."""
        temp_db.set_setting("dict_key", {"a": 1, "b": 2})
        result = temp_db.get_setting("dict_key")
        assert result == {"a": 1, "b": 2}

    def test_update_existing_setting(self, temp_db):
        """Test updating an existing setting."""
        temp_db.set_setting("update_key", "original")
        temp_db.set_setting("update_key", "updated")
        result = temp_db.get_setting("update_key")
        assert result == "updated"

    def test_delete_setting(self, temp_db):
        """Test deleting a setting."""
        temp_db.set_setting("delete_key", "value")
        temp_db.delete_setting("delete_key")
        result = temp_db.get_setting("delete_key")
        assert result is None

    def test_get_all_settings(self, temp_db):
        """Test getting all settings."""
        temp_db.set_setting("key1", "value1")
        temp_db.set_setting("key2", 42)
        temp_db.set_setting("key3", True)

        all_settings = temp_db.get_all_settings()

        assert all_settings["key1"] == "value1"
        assert all_settings["key2"] == 42
        assert all_settings["key3"] is True

    def test_clear_all_settings(self, temp_db):
        """Test clearing all settings."""
        temp_db.set_setting("key1", "value1")
        temp_db.set_setting("key2", "value2")

        temp_db.clear_all_settings()

        assert temp_db.get_setting("key1") is None
        assert temp_db.get_setting("key2") is None
        assert temp_db.get_all_settings() == {}
