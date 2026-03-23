"""Mapping of file extensions to human-readable document type labels."""

EXTENSION_TYPE_MAP = {
    # Documents
    ".pdf": "PDF Document",
    ".doc": "Legacy Word Document",
    ".docx": "Word Document",
    ".rtf": "Rich Text Document",
    ".txt": "Plain Text File",
    ".odt": "OpenDocument Text",
    ".pages": "Apple Pages Document",
    ".tex": "LaTeX Document",
    ".md": "Markdown Document",
    # Spreadsheets
    ".xls": "Legacy Excel Spreadsheet",
    ".xlsx": "Excel Spreadsheet",
    ".xlsm": "Excel Macro-Enabled Spreadsheet",
    ".xlsb": "Excel Binary Spreadsheet",
    ".csv": "CSV Spreadsheet",
    ".tsv": "Tab-Separated Values File",
    ".ods": "OpenDocument Spreadsheet",
    ".numbers": "Apple Numbers Spreadsheet",
    # Presentations
    ".ppt": "Legacy PowerPoint Presentation",
    ".pptx": "PowerPoint Presentation",
    ".odp": "OpenDocument Presentation",
    ".key": "Apple Keynote Presentation",
    # Images
    ".jpg": "JPEG Image",
    ".jpeg": "JPEG Image",
    ".png": "PNG Image",
    ".gif": "GIF Image",
    ".bmp": "Bitmap Image",
    ".svg": "SVG Vector Image",
    ".tif": "TIFF Image",
    ".tiff": "TIFF Image",
    ".webp": "WebP Image",
    ".ico": "Icon File",
    # Data / structured
    ".json": "JSON Data File",
    ".xml": "XML Document",
    ".yaml": "YAML Data File",
    ".yml": "YAML Data File",
    ".html": "HTML Document",
    ".htm": "HTML Document",
    ".xhtml": "XHTML Document",
    # Archives
    ".zip": "ZIP Archive",
    ".rar": "RAR Archive",
    ".7z": "7-Zip Archive",
    ".tar": "TAR Archive",
    ".gz": "Gzip Archive",
    ".bz2": "Bzip2 Archive",
    ".xz": "XZ Archive",
    # Audio / video
    ".mp3": "MP3 Audio",
    ".wav": "WAV Audio",
    ".mp4": "MP4 Video",
    ".avi": "AVI Video",
    ".mov": "QuickTime Video",
    ".wmv": "Windows Media Video",
    ".flv": "Flash Video",
    ".mkv": "Matroska Video",
    # E-books
    ".epub": "EPUB E-Book",
    ".mobi": "Kindle E-Book",
    # Code
    ".py": "Python Script",
    ".js": "JavaScript File",
    ".java": "Java Source File",
    ".c": "C Source File",
    ".cpp": "C++ Source File",
    ".sql": "SQL Script",
    ".sh": "Shell Script",
    # Other
    ".exe": "Windows Executable",
    ".dmg": "macOS Disk Image",
    ".iso": "Disk Image",
    ".msi": "Windows Installer",
    ".apk": "Android Package",
}


def get_document_type_label(extension: str) -> str:
    """Map a file extension to a human-readable document type label.

    Args:
        extension: The file extension, with or without leading dot.

    Returns:
        A human-readable label like "Excel Spreadsheet" or "XYZ File" for unknown types.
    """
    ext = extension.lower().strip()
    if not ext.startswith("."):
        ext = "." + ext
    return EXTENSION_TYPE_MAP.get(ext, f"{ext.lstrip('.').upper()} File")
