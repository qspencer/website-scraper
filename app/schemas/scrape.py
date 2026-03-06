from pydantic import BaseModel, HttpUrl, Field, field_validator
from typing import Optional, List
from urllib.parse import urlparse
from app.core.constants import DocumentTypeFilter, CrawlDepthOption
from app.schemas.document import DocumentInfo


class ScrapeRequest(BaseModel):
    url: str
    document_type_filter: DocumentTypeFilter = DocumentTypeFilter.COMMON
    crawl_option: CrawlDepthOption = CrawlDepthOption.SINGLE_PAGE
    max_depth: int = Field(default=2, ge=1)
    scan_all_pages: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()

        if not v:
            raise ValueError("Please enter a website address")

        if not v.startswith(("http://", "https://")):
            v = "https://" + v

        parsed = urlparse(v)

        # Must have a hostname
        if not parsed.hostname:
            raise ValueError("Please enter a valid website address (e.g. example.com)")

        hostname = parsed.hostname

        # Must contain at least one dot (e.g. "example.com"), unless it's localhost
        if "." not in hostname and hostname != "localhost":
            raise ValueError(
                "That doesn't look like a complete website address. "
                "Did you mean " + hostname + ".com?"
            )

        # No spaces in the URL
        if " " in v:
            raise ValueError(
                "The website address contains spaces. "
                "Please check for typos and try again"
            )

        # Reject obviously invalid TLDs (single char after last dot)
        parts = hostname.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) < 2 and hostname != "localhost":
            raise ValueError(
                "The website address doesn't have a valid ending (like .com or .org). "
                "Please check the address and try again"
            )

        return v

    @field_validator("max_depth")
    @classmethod
    def validate_max_depth(cls, v: int) -> int:
        from app.services.settings_service import runtime_settings
        max_allowed = runtime_settings.max_crawl_depth
        if v > max_allowed:
            raise ValueError(f"max_depth must be <= {max_allowed}")
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
    required_bytes: Optional[int] = None


class PathValidationResponse(BaseModel):
    valid: bool
    message: str
    absolute_path: Optional[str] = None
    error_code: Optional[str] = None  # "not_exists", "not_writable", "insufficient_space", "not_directory", "empty", "invalid"
    free_space_bytes: Optional[int] = None
    free_space_display: Optional[str] = None
