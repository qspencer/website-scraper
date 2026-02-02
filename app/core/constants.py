from enum import Enum
from typing import Dict, List, Set


class DocumentTypeFilter(str, Enum):
    COMMON = "common"
    ALL = "all"
    PDF_ONLY = "pdf_only"


class CrawlDepthOption(str, Enum):
    SINGLE_PAGE = "single"
    FOLLOW_LINKS = "follow"


# File extension definitions
COMMON_DOCUMENT_EXTENSIONS: Set[str] = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".txt", ".csv", ".rtf", ".odt"
}

ALL_FILE_EXTENSIONS: Set[str] = {
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".txt", ".csv", ".rtf", ".odt",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico",
    # Media
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac",
    # Other
    ".exe", ".dmg", ".iso", ".apk", ".deb", ".rpm",
    # Data
    ".json", ".xml", ".yaml", ".yml", ".sql"
}

PDF_ONLY_EXTENSIONS: Set[str] = {".pdf"}

# Mapping filter type to extensions
FILTER_EXTENSIONS: Dict[DocumentTypeFilter, Set[str]] = {
    DocumentTypeFilter.COMMON: COMMON_DOCUMENT_EXTENSIONS,
    DocumentTypeFilter.ALL: ALL_FILE_EXTENSIONS,
    DocumentTypeFilter.PDF_ONLY: PDF_ONLY_EXTENSIONS,
}

# File type icons for UI
FILE_TYPE_ICONS: Dict[str, str] = {
    ".pdf": "file-pdf",
    ".doc": "file-word",
    ".docx": "file-word",
    ".xls": "file-excel",
    ".xlsx": "file-excel",
    ".ppt": "file-powerpoint",
    ".pptx": "file-powerpoint",
    ".txt": "file-text",
    ".csv": "file-spreadsheet",
    ".zip": "file-archive",
    ".rar": "file-archive",
    ".7z": "file-archive",
    ".jpg": "file-image",
    ".jpeg": "file-image",
    ".png": "file-image",
    ".gif": "file-image",
    ".mp3": "file-audio",
    ".mp4": "file-video",
}

DEFAULT_FILE_ICON: str = "file"

# Human-readable filter names
FILTER_DISPLAY_NAMES: Dict[DocumentTypeFilter, str] = {
    DocumentTypeFilter.COMMON: "Common Documents (PDF, Word, Excel, etc.)",
    DocumentTypeFilter.ALL: "All Files",
    DocumentTypeFilter.PDF_ONLY: "PDFs Only",
}
