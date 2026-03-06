import pytest
import os
import tempfile
from app.utils.file_utils import (
    format_file_size,
    validate_download_path,
    ensure_directory_exists,
    get_unique_filename,
    sanitize_filename,
)


class TestFormatFileSize:
    def test_bytes(self):
        assert format_file_size(500) == "500 B"

    def test_kilobytes(self):
        assert format_file_size(1024) == "1.0 KB"

    def test_megabytes(self):
        assert format_file_size(1048576) == "1.0 MB"

    def test_gigabytes(self):
        assert format_file_size(1073741824) == "1.0 GB"

    def test_zero(self):
        assert format_file_size(0) == "0 B"

    def test_none(self):
        assert format_file_size(None) is None

    def test_negative(self):
        assert format_file_size(-100) is None

    def test_fractional_mb(self):
        assert format_file_size(2621440) == "2.5 MB"


class TestValidateDownloadPath:
    def test_existing_writable_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_download_path(tmpdir)
            assert result["valid"] is True
            assert result["absolute_path"] == tmpdir
            assert result["free_space_bytes"] is not None

    def test_nonexistent_but_creatable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = os.path.join(tmpdir, "new_folder")
            result = validate_download_path(new_path)
            assert result["valid"] is True
            assert result["error_code"] == "not_exists"
            assert "will be created" in result["message"].lower()

    def test_empty_path(self):
        result = validate_download_path("")
        assert result["valid"] is False
        assert result["error_code"] == "empty"

    def test_expands_home(self):
        result = validate_download_path("~/downloads")
        if result["absolute_path"]:
            assert "~" not in result["absolute_path"]

    def test_insufficient_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Request more bytes than could possibly be free
            result = validate_download_path(tmpdir, required_bytes=2**62)
            assert result["valid"] is False
            assert result["error_code"] == "insufficient_space"

    def test_sufficient_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_download_path(tmpdir, required_bytes=1)
            assert result["valid"] is True


class TestEnsureDirectoryExists:
    def test_creates_new_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = os.path.join(tmpdir, "new_folder")
            success, result = ensure_directory_exists(new_path)
            assert success is True
            assert os.path.exists(new_path)

    def test_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            success, result = ensure_directory_exists(tmpdir)
            assert success is True

    def test_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = os.path.join(tmpdir, "a", "b", "c")
            success, result = ensure_directory_exists(new_path)
            assert success is True
            assert os.path.exists(new_path)


class TestGetUniqueFilename:
    def test_no_conflict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_unique_filename(tmpdir, "file.pdf")
            assert result == "file.pdf"

    def test_with_conflict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create existing file
            open(os.path.join(tmpdir, "file.pdf"), "w").close()
            result = get_unique_filename(tmpdir, "file.pdf")
            assert result == "file_1.pdf"

    def test_multiple_conflicts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple existing files
            open(os.path.join(tmpdir, "file.pdf"), "w").close()
            open(os.path.join(tmpdir, "file_1.pdf"), "w").close()
            open(os.path.join(tmpdir, "file_2.pdf"), "w").close()
            result = get_unique_filename(tmpdir, "file.pdf")
            assert result == "file_3.pdf"


class TestSanitizeFilename:
    def test_valid_filename(self):
        assert sanitize_filename("document.pdf") == "document.pdf"

    def test_removes_invalid_chars(self):
        result = sanitize_filename('file<>:"/\\|?*.pdf')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_strips_spaces(self):
        assert sanitize_filename("  file.pdf  ") == "file.pdf"

    def test_empty_becomes_unnamed(self):
        result = sanitize_filename("")
        assert result == "unnamed_file"

    def test_truncates_long_name(self):
        long_name = "a" * 300 + ".pdf"
        result = sanitize_filename(long_name)
        assert len(result) <= 200
        assert result.endswith(".pdf")
