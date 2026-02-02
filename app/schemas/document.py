from pydantic import BaseModel, ConfigDict
from typing import Optional


class DocumentInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://example.com/report.pdf",
                "filename": "report.pdf",
                "extension": ".pdf",
                "file_size_bytes": 1048576,
                "file_size_display": "1.0 MB",
                "source_page": "https://example.com/documents",
                "depth": 0,
                "is_accessible": True
            }
        }
    )

    url: str
    filename: str
    extension: str
    file_size_bytes: Optional[int] = None
    file_size_display: Optional[str] = None
    source_page: str
    depth: int = 0
    is_accessible: bool = True
    error_message: Optional[str] = None


class DownloadProgress(BaseModel):
    status: str  # "downloading", "complete", "error"
    current_file: Optional[str] = None
    current_file_index: int = 0
    total_files: int = 0
    bytes_downloaded: int = 0
    total_bytes: Optional[int] = None
    files_completed: int = 0
    files_failed: int = 0
    message: str = ""
