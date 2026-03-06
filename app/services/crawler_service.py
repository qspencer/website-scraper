import asyncio
from collections import deque
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple
import time

from app.core.constants import DocumentTypeFilter, CrawlDepthOption
from app.core.logging_config import get_logger
from app.schemas.document import DocumentInfo
from app.schemas.scrape import ScrapeProgress, ScrapeResult
from app.services.scraper_service import scraper_service, clear_inaccessible_log_cache
from app.services.settings_service import runtime_settings
from app.utils.url_utils import (
    get_domain, is_internal_link, normalize_url,
    normalize_url_for_crawl, should_skip_url, get_filename_from_url
)

logger = get_logger(__name__)


@dataclass
class PageScanResult:
    """Result of scanning a single page."""
    url: str
    depth: int
    page_links: List[str]
    documents: List[DocumentInfo]
    warning: Optional[str]
    error: Optional[str] = None


class CrawlState:
    """Holds the state of a crawl that can be resumed."""
    def __init__(self):
        self.visited_urls: Set[str] = set()  # Normalized URLs we've visited
        self.queued_urls: Set[str] = set()  # Normalized URLs in the queue (not yet visited)
        self.document_urls: Set[str] = set()  # Document URLs found
        self.document_filenames: Set[str] = set()  # Filenames for deduplication
        self.pending_queue: List[Tuple[str, int]] = []  # List of (url, depth)
        self.all_documents: List[DocumentInfo] = []
        self.pages_scanned: int = 0
        self.batches_completed: int = 0  # Track batch number for progress display
        self.errors: List[str] = []
        self.failed_pages: List[Tuple[str, int]] = []  # (url, depth) of pages that failed to scan
        self.skipped_duplicates: int = 0  # Track skipped duplicate files


class CrawlerService:
    def __init__(self):
        pass

    @property
    def max_concurrent(self) -> int:
        return runtime_settings.max_concurrent_requests

    @property
    def rate_limit_delay(self) -> float:
        return 1.0 / runtime_settings.requests_per_second

    async def _scan_page_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        session,
        url: str,
        depth: int,
        filter_type: DocumentTypeFilter,
    ) -> PageScanResult:
        """Scan a single page with semaphore-controlled concurrency."""
        async with semaphore:
            # Small delay to spread out requests
            await asyncio.sleep(self.rate_limit_delay)
            try:
                page_links, documents, warning = await scraper_service.scan_page(
                    session, url, filter_type, depth
                )
                return PageScanResult(
                    url=url,
                    depth=depth,
                    page_links=page_links,
                    documents=documents,
                    warning=warning,
                )
            except asyncio.CancelledError:
                logger.warning(f"Scan cancelled for {url}")
                return PageScanResult(
                    url=url,
                    depth=depth,
                    page_links=[],
                    documents=[],
                    warning=None,
                    error="Request cancelled",
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout scanning {url}")
                return PageScanResult(
                    url=url,
                    depth=depth,
                    page_links=[],
                    documents=[],
                    warning=None,
                    error="Request timed out",
                )
            except Exception as e:
                logger.error(f"Error scanning {url}: {type(e).__name__}: {e}")
                return PageScanResult(
                    url=url,
                    depth=depth,
                    page_links=[],
                    documents=[],
                    warning=None,
                    error=str(e),
                )

    async def crawl(
        self,
        start_url: str,
        filter_type: DocumentTypeFilter,
        crawl_option: CrawlDepthOption,
        max_depth: int,
        state: Optional[CrawlState] = None,
        scan_all_pages: bool = False,
    ) -> AsyncGenerator[ScrapeProgress | ScrapeResult, None]:
        """
        Crawl a website and yield progress updates.

        Args:
            start_url: The URL to start crawling from
            filter_type: Type of documents to look for
            crawl_option: Whether to follow internal links
            max_depth: Maximum depth to crawl
            state: Optional existing state to resume from

        Yields ScrapeProgress during crawling, then ScrapeResult at the end.
        """
        # Create state if not provided
        if state is None:
            state = CrawlState()

        # Determine if this is a continuation (state has pending pages from previous batch)
        is_continuation = len(state.pending_queue) > 0

        if not is_continuation:
            # Clear the inaccessible URL log cache for a fresh scan
            clear_inaccessible_log_cache()

        if is_continuation:
            logger.info(f"Resuming crawl: {len(state.pending_queue)} pages remaining, "
                       f"{state.pages_scanned} already scanned, "
                       f"concurrent requests: {self.max_concurrent}")
        else:
            logger.info(f"Starting crawl: url={start_url}, filter={filter_type.value}, "
                       f"crawl_option={crawl_option.value}, max_depth={max_depth}, "
                       f"concurrent requests: {self.max_concurrent}")

        try:
            start_url = normalize_url(start_url)
        except Exception as e:
            logger.error(f"Invalid start URL '{start_url}': {e}")
            yield ScrapeResult(
                success=False,
                documents=[],
                pages_scanned=0,
                errors=[f"Invalid URL: {str(e)}"]
            )
            return

        base_domain = get_domain(start_url)
        effective_max_depth = 0 if crawl_option == CrawlDepthOption.SINGLE_PAGE else max_depth
        max_pages_per_batch = runtime_settings.max_pages_per_scan
        pages_this_batch = 0

        # Initialize queue
        if is_continuation:
            queue: deque = deque(state.pending_queue)
            state.pending_queue = []  # Clear pending, we'll repopulate if needed
        else:
            queue = deque([(start_url, 0)])

        # Semaphore for concurrent request limiting
        semaphore = asyncio.Semaphore(self.max_concurrent)

        try:
            async with await scraper_service.create_session() as session:
                while queue:
                    # Check batch limit (skip if scanning all pages)
                    if not scan_all_pages and pages_this_batch >= max_pages_per_batch:
                        # Save remaining queue to state for continuation
                        state.pending_queue = list(queue)
                        logger.info(f"Batch limit reached ({max_pages_per_batch}), "
                                   f"{len(queue)} pages remaining")
                        break

                    # Grab a batch of URLs to process concurrently
                    if scan_all_pages:
                        batch_size = min(self.max_concurrent, len(queue))
                    else:
                        batch_size = min(
                            self.max_concurrent,
                            len(queue),
                            max_pages_per_batch - pages_this_batch
                        )

                    batch: List[Tuple[str, int]] = []
                    while len(batch) < batch_size and queue:
                        url, depth = queue.popleft()

                        # Skip URLs that should be ignored (comment replies, feeds, etc.)
                        if should_skip_url(url):
                            logger.debug(f"Skipping URL (matches skip pattern): {url[:80]}")
                            continue

                        # Normalize URL for deduplication
                        normalized_url = normalize_url_for_crawl(url)

                        # Skip already visited (after normalization)
                        if normalized_url in state.visited_urls:
                            if normalized_url != url:
                                logger.debug(f"Skipping duplicate URL: {url[:60]} (normalized to existing)")
                            continue
                        if depth > effective_max_depth:
                            continue

                        # Move from queued to visited
                        state.queued_urls.discard(normalized_url)
                        state.visited_urls.add(normalized_url)
                        batch.append((url, depth))

                    if not batch:
                        continue

                    # Yield progress before processing batch
                    batch_num = state.batches_completed + 1
                    pages_remaining = len(queue) + len(batch)
                    yield ScrapeProgress(
                        status="scanning",
                        current_page=f"Processing batch {batch_num}...",
                        pages_scanned=state.pages_scanned,
                        total_pages_queued=len(queue),
                        documents_found=len(state.all_documents),
                        message=f"Scanning batch {batch_num} ({len(batch)} pages, {pages_remaining:,} remaining)...",
                    )

                    # Process batch concurrently
                    logger.debug(f"Starting batch of {len(batch)} pages (pages scanned: {state.pages_scanned})")
                    tasks = [
                        self._scan_page_with_semaphore(
                            semaphore, session, url, depth, filter_type
                        )
                        for url, depth in batch
                    ]

                    try:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                    except Exception as e:
                        logger.error(f"Batch gather failed: {type(e).__name__}: {e}", exc_info=True)
                        # Continue with empty results for this batch
                        results = []

                    # Process results
                    errors_in_batch = 0
                    for i, result in enumerate(results):
                        state.pages_scanned += 1
                        pages_this_batch += 1

                        # Handle exceptions from gather
                        if isinstance(result, Exception):
                            url = batch[i][0] if i < len(batch) else "unknown"
                            depth = batch[i][1] if i < len(batch) else 0
                            error_msg = f"Exception scanning {url}: {type(result).__name__}: {result}"
                            logger.error(error_msg)
                            state.errors.append(error_msg)
                            state.failed_pages.append((url, depth))
                            errors_in_batch += 1
                            continue

                        if result.error:
                            error_msg = f"Error scanning {result.url}: {result.error}"
                            logger.error(error_msg)
                            state.errors.append(error_msg)
                            state.failed_pages.append((result.url, result.depth))
                            errors_in_batch += 1
                            continue

                        # Add warning to errors if this is the first page
                        if result.warning and state.pages_scanned == 1:
                            state.errors.append(result.warning)

                        # Add new documents (deduplicate by URL and filename)
                        for doc in result.documents:
                            # Skip if we've seen this URL
                            if doc.url in state.document_urls:
                                continue
                            # Skip if we've seen this filename (same file, different URL)
                            filename_lower = doc.filename.lower()
                            if filename_lower in state.document_filenames:
                                state.skipped_duplicates += 1
                                logger.info(f"Skipping duplicate file: {doc.filename} (URL: {doc.url[:60]}...)")
                                continue
                            state.document_urls.add(doc.url)
                            state.document_filenames.add(filename_lower)
                            state.all_documents.append(doc)

                        # Add new links to queue (with URL normalization)
                        if crawl_option == CrawlDepthOption.FOLLOW_LINKS and result.depth < effective_max_depth:
                            new_links = 0
                            skipped_links = 0
                            external_links = 0
                            for link in result.page_links:
                                # Skip URLs that should be ignored
                                if should_skip_url(link):
                                    logger.debug(f"Skipping link (matches skip pattern): {link[:80]}")
                                    skipped_links += 1
                                    continue
                                # Normalize URL to avoid duplicates
                                normalized_link = normalize_url_for_crawl(link)
                                # Skip if already visited or already in queue
                                if normalized_link in state.visited_urls or normalized_link in state.queued_urls:
                                    skipped_links += 1
                                    continue
                                if is_internal_link(link, start_url):
                                    queue.append((link, result.depth + 1))
                                    state.queued_urls.add(normalized_link)  # Track that it's queued
                                    new_links += 1
                                else:
                                    external_links += 1
                            logger.info(f"Links from {result.url[:50]}: {new_links} added, {skipped_links} skipped, {external_links} external")

                    # Increment batch counter
                    state.batches_completed += 1

                    # Log batch summary
                    if errors_in_batch > 0:
                        logger.warning(f"Batch {state.batches_completed} complete: {len(batch)} pages, {errors_in_batch} errors, "
                                      f"{len(state.all_documents)} total docs, {len(queue)} queued")
                    else:
                        logger.debug(f"Batch {state.batches_completed} complete: {len(batch)} pages, "
                                    f"{len(state.all_documents)} total docs, {len(queue)} queued")

                    # Yield progress after batch
                    yield ScrapeProgress(
                        status="scanning",
                        current_page=f"Completed batch {state.batches_completed}",
                        pages_scanned=state.pages_scanned,
                        total_pages_queued=len(queue),
                        documents_found=len(state.all_documents),
                        message=f"Batch {state.batches_completed} done - {state.pages_scanned:,} pages scanned, {len(state.all_documents)} docs found",
                    )

        except asyncio.CancelledError:
            logger.warning("Crawl was cancelled")
            # Save current queue for potential resume
            state.pending_queue = list(queue) if queue else []
            state.errors.append("Crawl was cancelled by user or timeout")
        except Exception as e:
            error_msg = f"Crawl failed: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            state.errors.append(error_msg)

        has_more = len(state.pending_queue) > 0

        logger.info(f"Crawl batch complete: {state.pages_scanned} total pages scanned, "
                   f"{len(state.all_documents)} documents found, "
                   f"{state.skipped_duplicates} duplicate files skipped, "
                   f"{len(state.pending_queue)} pages remaining")

        yield ScrapeResult(
            success=True,
            documents=state.all_documents,
            pages_scanned=state.pages_scanned,
            errors=state.errors,
            has_more_pages=has_more,
            pages_remaining=len(state.pending_queue),
        )


crawler_service = CrawlerService()
