import asyncio
import os
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Optional, Tuple

from app.core.config import settings
from app.core.constants import DocumentTypeFilter, FILTER_EXTENSIONS
from app.core.logging_config import get_logger, get_inaccessible_docs_logger
from app.schemas.document import DocumentInfo
from app.utils.url_utils import (
    normalize_url,
    get_filename_from_url,
    get_extension_from_url,
    is_likely_html_page,
)
from app.utils.file_utils import format_file_size
from app.services.browser_service import fetch_page_with_browser

logger = get_logger(__name__)
inaccessible_logger = get_inaccessible_docs_logger()

# Track URLs already logged as inaccessible to avoid duplicate log entries
_logged_inaccessible_urls: set = set()


def log_inaccessible(reason: str, url: str) -> None:
    """Log an inaccessible URL, skipping duplicates."""
    if url not in _logged_inaccessible_urls:
        _logged_inaccessible_urls.add(url)
        inaccessible_logger.info(f"{reason} | {url}")


def clear_inaccessible_log_cache() -> None:
    """Clear the inaccessible URL cache and log file (call at start of new scan)."""
    _logged_inaccessible_urls.clear()

    # Clear the log file
    log_file = os.path.join("logs", "inaccessible_documents.log")
    if os.path.exists(log_file):
        try:
            # Truncate the file
            open(log_file, 'w').close()
            logger.debug("Cleared inaccessible documents log file")
        except Exception as e:
            logger.warning(f"Failed to clear inaccessible log file: {e}")


class ScraperService:
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT)
        self.headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def create_session(self) -> aiohttp.ClientSession:
        """Create an aiohttp session with proper settings."""
        connector = aiohttp.TCPConnector(
            limit=settings.MAX_CONCURRENT_REQUESTS,
            limit_per_host=5,
        )
        logger.debug(f"Created HTTP session with {settings.MAX_CONCURRENT_REQUESTS} max connections")
        return aiohttp.ClientSession(
            timeout=self.timeout,
            headers=self.headers,
            connector=connector,
        )

    async def fetch_page(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch HTML content from a URL.

        Returns:
            Tuple of (html_content, error_message)
        """
        logger.debug(f"Fetching page: {url}")
        try:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    logger.warning(f"Non-200 response for {url}: HTTP {response.status}")
                    return None, f"HTTP {response.status}"

                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    logger.debug(f"Skipping non-HTML content at {url}: {content_type}")
                    return None, "Not an HTML page"

                html = await response.text()
                logger.debug(f"Successfully fetched {url} ({len(html)} bytes)")
                return html, None

        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching {url}")
            return None, "Request timed out"
        except aiohttp.ClientError as e:
            logger.warning(f"Connection error fetching {url}: {e}")
            return None, f"Connection error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {e}", exc_info=True)
            return None, f"Error: {str(e)}"

    def is_javascript_redirect(self, html: str) -> bool:
        """Check if the page is a JavaScript-only redirect or empty page."""
        if not html:
            return False
        if len(html) < 500:
            # Very short HTML is suspicious
            lower_html = html.lower()
            js_redirect_patterns = [
                "window.location",
                "window.onload",
                "document.location",
                "location.href",
                "location.replace",
            ]
            return any(pattern in lower_html for pattern in js_redirect_patterns)
        return False

    def parse_links(self, html: str, base_url: str) -> List[str]:
        """Extract all links from HTML content."""
        links = []

        try:
            soup = BeautifulSoup(html, "lxml")

            # Find all anchor tags
            for tag in soup.find_all("a", href=True):
                href = tag.get("href", "").strip()
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    try:
                        full_url = normalize_url(href, base_url)
                        links.append(full_url)
                    except Exception as e:
                        logger.debug(f"Failed to normalize URL '{href}': {e}")
                        continue

            # Also check for direct file links in other tags
            for tag in soup.find_all(["embed", "object", "iframe"], src=True):
                src = tag.get("src", "").strip()
                if src:
                    try:
                        full_url = normalize_url(src, base_url)
                        links.append(full_url)
                    except Exception as e:
                        logger.debug(f"Failed to normalize src '{src}': {e}")
                        continue

            unique_links = list(set(links))
            logger.debug(f"Extracted {len(unique_links)} unique links from {base_url}")
            return unique_links

        except Exception as e:
            logger.error(f"Failed to parse HTML from {base_url}: {e}", exc_info=True)
            return []

    def filter_document_links(
        self, links: List[str], filter_type: DocumentTypeFilter
    ) -> List[str]:
        """Filter links to only document types based on filter."""
        allowed_extensions = FILTER_EXTENSIONS.get(filter_type, set())
        document_links = []

        for link in links:
            ext = get_extension_from_url(link)
            if ext and ext.lower() in allowed_extensions:
                document_links.append(link)

        logger.debug(f"Found {len(document_links)} document links (filter: {filter_type.value})")
        return document_links

    def filter_page_links(self, links: List[str]) -> List[str]:
        """Filter links to only those that are likely HTML pages."""
        return [link for link in links if is_likely_html_page(link)]

    async def get_file_info(
        self, session: aiohttp.ClientSession, url: str, source_page: str, depth: int
    ) -> DocumentInfo:
        """Get file information via HEAD request."""
        filename = get_filename_from_url(url)
        extension = get_extension_from_url(url)

        doc_info = DocumentInfo(
            url=url,
            filename=filename,
            extension=extension,
            source_page=source_page,
            depth=depth,
        )

        try:
            async with session.head(url, allow_redirects=True) as response:
                if response.status == 200:
                    # Get content length
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            size_bytes = int(content_length)
                            doc_info.file_size_bytes = size_bytes
                            doc_info.file_size_display = format_file_size(size_bytes)
                        except ValueError:
                            logger.info(f"SIZE UNKNOWN (invalid Content-Length) | {url}")
                    else:
                        logger.info(f"SIZE UNKNOWN (no Content-Length header) | {url}")

                    # Update filename from Content-Disposition if available. Best-effort
                    # parse; a malformed header is non-fatal because we already have a
                    # fallback filename derived from the URL.
                    content_disp = response.headers.get("Content-Disposition", "")
                    if "filename=" in content_disp:
                        try:
                            fn = content_disp.split("filename=")[1].strip('"\'')
                            if fn:
                                doc_info.filename = fn
                        except (IndexError, AttributeError) as e:
                            logger.debug(f"Could not parse Content-Disposition {content_disp!r}: {e}")

                    doc_info.is_accessible = True
                    logger.debug(f"File info retrieved: {filename} ({doc_info.file_size_display or 'unknown size'})")
                else:
                    doc_info.is_accessible = False
                    doc_info.error_message = f"HTTP {response.status}"
                    logger.debug(f"File not accessible: {url} (HTTP {response.status})")
                    log_inaccessible(f"HTTP {response.status}", url)

        except asyncio.TimeoutError:
            doc_info.is_accessible = False
            doc_info.error_message = "Timeout"
            logger.debug(f"Timeout getting file info: {url}")
            log_inaccessible("TIMEOUT", url)
        except Exception as e:
            doc_info.is_accessible = False
            doc_info.error_message = str(e)[:50]
            logger.debug(f"Error getting file info for {url}: {e}")
            log_inaccessible(f"ERROR: {str(e)[:100]}", url)

        return doc_info

    async def get_file_size_via_get(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[int]:
        """
        Get file size using GET request with Range header.
        This is more reliable than HEAD for servers that don't return Content-Length.
        """
        try:
            # Request just the first byte to get Content-Range header
            headers = {**self.headers, "Range": "bytes=0-0"}
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 206:  # Partial Content
                    # Content-Range format: "bytes 0-0/12345" where 12345 is total size
                    content_range = response.headers.get("Content-Range", "")
                    if "/" in content_range:
                        try:
                            total_size = int(content_range.split("/")[1])
                            logger.debug(f"Got size via Range request: {url} = {total_size} bytes")
                            return total_size
                        except (ValueError, IndexError):
                            logger.debug(f"Invalid Content-Range format: {content_range}")
                elif response.status == 200:
                    # Server doesn't support Range, but might have Content-Length
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            size = int(content_length)
                            logger.debug(f"Got size via GET Content-Length: {url} = {size} bytes")
                            return size
                        except ValueError:
                            pass
                    else:
                        # Log first few failures at INFO level
                        logger.info(f"No Content-Length in 200 response: {url}")
                else:
                    logger.info(f"Unexpected status {response.status} for size check: {url}")
                    # Log to dedicated inaccessible docs file
                    log_inaccessible(f"HTTP {response.status}", url)
        except asyncio.TimeoutError:
            logger.info(f"Timeout getting file size: {url}")
            log_inaccessible("TIMEOUT", url)
        except Exception as e:
            logger.info(f"Error getting file size for {url}: {e}")
            log_inaccessible(f"ERROR: {e}", url)

        return None

    async def calculate_file_sizes(
        self, urls: List[str], max_concurrent: int = 10
    ) -> dict:
        """
        Calculate file sizes for a list of URLs.
        Returns dict mapping URL to size in bytes (None if unknown).
        """
        results = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def get_size_with_semaphore(url: str):
            async with semaphore:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers=self.headers
                ) as session:
                    size = await self.get_file_size_via_get(session, url)
                    return url, size

        tasks = [get_size_with_semaphore(url) for url in urls]

        for coro in asyncio.as_completed(tasks):
            try:
                url, size = await coro
                results[url] = size
            except Exception as e:
                logger.debug(f"Error in size calculation: {e}")

        return results

    async def scan_page(
        self,
        session: aiohttp.ClientSession,
        url: str,
        filter_type: DocumentTypeFilter,
        depth: int,
    ) -> Tuple[List[str], List[DocumentInfo], Optional[str]]:
        """
        Scan a single page for documents and links.
        Automatically uses headless browser if JavaScript rendering is needed.

        Returns:
            Tuple of (page_links, document_infos, warning_message)
        """
        logger.info(f"Scanning page: {url} (depth={depth})")

        # First, try fast HTTP fetch
        html, error = await self.fetch_page(session, url)

        if error or not html:
            logger.warning(f"Could not fetch page {url}: {error}")
            return [], [], error

        # Check if we need to use browser (JS redirect or minimal content)
        needs_browser = False
        if self.is_javascript_redirect(html):
            logger.info(f"JavaScript redirect detected on {url}, switching to browser")
            needs_browser = True
        else:
            all_links = self.parse_links(html, url)
            if len(all_links) == 0 and len(html) < 2000:
                logger.info(f"Minimal content on {url}, trying browser")
                needs_browser = True

        # Retry with browser if needed
        if needs_browser:
            browser_html, browser_error = await fetch_page_with_browser(url)
            if browser_html and not browser_error:
                html = browser_html
                logger.info(f"Browser fetch successful for {url}")
            else:
                logger.warning(f"Browser fetch also failed for {url}: {browser_error}")

        # Parse the HTML (either from HTTP or browser)
        all_links = self.parse_links(html, url)

        # Get document links
        document_links = self.filter_document_links(all_links, filter_type)

        # Get page links for further crawling
        page_links = self.filter_page_links(all_links)

        # Get info for each document
        documents = []
        tasks = [
            self.get_file_info(session, doc_url, url, depth)
            for doc_url in document_links
        ]

        if tasks:
            logger.debug(f"Fetching info for {len(tasks)} documents")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, DocumentInfo):
                    documents.append(result)
                elif isinstance(result, Exception):
                    logger.error(f"Error getting document info: {result}")

        logger.info(f"Page scan complete: {url} - {len(documents)} documents, {len(page_links)} links")
        return page_links, documents, None


scraper_service = ScraperService()
