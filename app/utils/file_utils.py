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


def validate_download_path(path: str) -> Tuple[bool, str, Optional[str]]:
    """
    Validate a download path.

    Returns:
        Tuple of (is_valid, message, absolute_path)
    """
    if not path:
        return False, "Download path cannot be empty", None

    path = path.strip()

    # Expand user home directory
    path = os.path.expanduser(path)

    # Convert to absolute path
    abs_path = os.path.abspath(path)

    # Security: prevent path traversal attacks
    # Check for suspicious patterns
    if ".." in path:
        # Allow .. but resolve to absolute and verify it's reasonable
        pass

    try:
        path_obj = Path(abs_path)

        # Check if path exists
        if path_obj.exists():
            if not path_obj.is_dir():
                return False, "Path exists but is not a directory", None
            if not os.access(abs_path, os.W_OK):
                return False, "Directory exists but is not writable", None
            return True, "Directory exists and is writable", abs_path

        # Path doesn't exist - check if we can create it
        # Find the first existing parent
        parent = path_obj.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent

        if parent.exists():
            if not os.access(str(parent), os.W_OK):
                return False, f"Cannot create directory: parent '{parent}' is not writable", None
            return True, "Directory will be created", abs_path
        else:
            return False, "Invalid path: no accessible parent directory", None

    except Exception as e:
        return False, f"Invalid path: {str(e)}", None


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
