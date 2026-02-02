from pydantic import BaseModel, HttpUrl, Field, field_validator
from typing import Optional, List
from app.core.constants import DocumentTypeFilter, CrawlDepthOption
from app.schemas.document import DocumentInfo


class ScrapeRequest(BaseModel):
    url: str
    document_type_filter: DocumentTypeFilter = DocumentTypeFilter.COMMON
    crawl_option: CrawlDepthOption = CrawlDepthOption.SINGLE_PAGE
    max_depth: int = Field(default=2, ge=1, le=5)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v


class ScrapeStartResponse(BaseModel):
    session_id: str
    status: str
    message: str


class ScrapeProgress(BaseModel):
    status: str  # "scanning", "complete", "error"
    current_page: Optional[str] = None
    pages_scanned: int = 0
    total_pages_queued: int = 0
    documents_found: int = 0
    message: str = ""


class ScrapeResult(BaseModel):
    success: bool
    documents: List[DocumentInfo] = []
    pages_scanned: int = 0
    errors: List[str] = []
    has_more_pages: bool = False
    pages_remaining: int = 0


class DownloadRequest(BaseModel):
    session_id: str
    document_urls: List[str]
    download_path: str

    @field_validator("download_path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        return v.strip()


class DownloadStartResponse(BaseModel):
    session_id: str
    status: str
    message: str


class PathValidationRequest(BaseModel):
    path: str


class PathValidationResponse(BaseModel):
    valid: bool
    message: str
    absolute_path: Optional[str] = None
