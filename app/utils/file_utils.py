import os
from pathlib import Path
from typing import Tuple, Optional


def format_file_size(size_bytes: Optional[int]) -> Optional[str]:
    """Format file size in human-readable format."""
    if size_bytes is None:
        return None

    if size_bytes < 0:
        return None

    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} B"

    return f"{size:.1f} {units[unit_index]}"


def validate_download_path(path: str, required_bytes: int = 0) -> dict:
    """
    Validate a download path.

    Returns:
        dict with keys: valid, message, absolute_path, error_code, free_space_bytes, free_space_display
    """
    result = {
        "valid": False,
        "message": "",
        "absolute_path": None,
        "error_code": None,
        "free_space_bytes": None,
        "free_space_display": None,
    }

    if not path:
        result["message"] = "Download path cannot be empty"
        result["error_code"] = "empty"
        return result

    path = path.strip()

    # Expand user home directory
    path = os.path.expanduser(path)

    # Convert to absolute path
    abs_path = os.path.abspath(path)
    result["absolute_path"] = abs_path

    try:
        path_obj = Path(abs_path)

        # Check if path exists
        if path_obj.exists():
            if not path_obj.is_dir():
                result["message"] = "Path exists but is not a directory"
                result["error_code"] = "not_directory"
                return result
            if not os.access(abs_path, os.W_OK):
                result["message"] = "Directory exists but is not writable"
                result["error_code"] = "not_writable"
                return result
            # Directory exists and is writable - check space
            return _check_disk_space(result, abs_path, required_bytes)

        # Path doesn't exist - check if parent is writable
        parent = path_obj.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent

        if parent.exists():
            if not os.access(str(parent), os.W_OK):
                result["message"] = f"Cannot create directory: parent '{parent}' is not writable"
                result["error_code"] = "not_writable"
                return result
            # Parent writable, directory will need to be created
            result["error_code"] = "not_exists"
            result["message"] = "Directory does not exist"
            # Check space on the parent that exists
            return _check_disk_space(result, str(parent), required_bytes, dir_needs_creation=True)
        else:
            result["message"] = "Invalid path: no accessible parent directory"
            result["error_code"] = "invalid"
            return result

    except Exception as e:
        result["message"] = f"Invalid path: {str(e)}"
        result["error_code"] = "invalid"
        return result


def _check_disk_space(result: dict, check_path: str, required_bytes: int, dir_needs_creation: bool = False) -> dict:
    """Check disk space and update result dict."""
    try:
        stat = os.statvfs(check_path)
        free_bytes = stat.f_bavail * stat.f_frsize
        result["free_space_bytes"] = free_bytes
        result["free_space_display"] = format_file_size(free_bytes)

        if required_bytes > 0 and free_bytes < required_bytes:
            result["valid"] = False
            result["message"] = f"Not enough disk space. Need {format_file_size(required_bytes)}, only {format_file_size(free_bytes)} available"
            result["error_code"] = "insufficient_space"
            return result
    except OSError:
        pass  # Can't check space, proceed anyway

    if dir_needs_creation:
        result["valid"] = True
        result["message"] = "Directory will be created"
        result["error_code"] = "not_exists"
    else:
        result["valid"] = True
        result["message"] = "Directory exists and is writable"
    return result


def ensure_directory_exists(path: str) -> Tuple[bool, str]:
    """
    Ensure a directory exists, creating it if necessary.

    Returns:
        Tuple of (success, message)
    """
    try:
        path = os.path.expanduser(path)
        abs_path = os.path.abspath(path)
        os.makedirs(abs_path, exist_ok=True)
        return True, abs_path
    except PermissionError:
        return False, "Permission denied"
    except Exception as e:
        return False, str(e)


def get_unique_filename(directory: str, filename: str) -> str:
    """
    Get a unique filename in a directory by appending a number if necessary.
    """
    filepath = os.path.join(directory, filename)

    if not os.path.exists(filepath):
        return filename

    name, ext = os.path.splitext(filename)
    counter = 1

    while True:
        new_filename = f"{name}_{counter}{ext}"
        new_filepath = os.path.join(directory, new_filename)
        if not os.path.exists(new_filepath):
            return new_filename
        counter += 1

        # Safety limit
        if counter > 10000:
            raise ValueError("Too many files with same name")


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing/replacing invalid characters.
    """
    # Replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Remove leading/trailing spaces and dots
    filename = filename.strip(" .")

    # Ensure filename is not empty
    if not filename:
        filename = "unnamed_file"

    # Limit length (255 is common filesystem limit)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[: 200 - len(ext)] + ext

    return filename
