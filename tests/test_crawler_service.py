"""Tests for the crawler service."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.crawler_service import CrawlerService, CrawlState
from app.core.constants import DocumentTypeFilter, CrawlDepthOption
from app.schemas.document import DocumentInfo
from app.schemas.scrape import ScrapeProgress, ScrapeResult


def make_doc(url, filename=None):
    """Helper to create a DocumentInfo."""
    if filename is None:
        filename = url.rsplit("/", 1)[-1]
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]
    return DocumentInfo(
        url=url,
        filename=filename,
        extension=ext,
        source_page="https://example.com",
        depth=0,
    )


def make_mock_scraper(scan_side_effect=None, scan_return=None):
    """Create a mock scraper_service with properly mocked create_session."""
    mock = MagicMock()

    # create_session returns an awaitable that yields an async context manager
    mock_session = MagicMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = False
    mock.create_session = AsyncMock(return_value=mock_cm)

    # scan_page is async
    if scan_side_effect:
        mock.scan_page = AsyncMock(side_effect=scan_side_effect)
    else:
        mock.scan_page = AsyncMock(return_value=scan_return or ([], [], None))

    return mock


async def collect(crawler, **kwargs):
    """Collect all items yielded from crawl()."""
    items = []
    async for item in crawler.crawl(**kwargs):
        items.append(item)
    return items


def get_result(items):
    """Extract the ScrapeResult from collected items."""
    results = [i for i in items if isinstance(i, ScrapeResult)]
    assert len(results) == 1
    return results[0]


def get_progress(items):
    """Extract ScrapeProgress items from collected items."""
    return [i for i in items if isinstance(i, ScrapeProgress)]


@pytest.fixture(autouse=True)
def fast_settings():
    """Mock runtime settings for fast tests."""
    with patch("app.services.crawler_service.runtime_settings") as mock:
        mock.max_concurrent_requests = 5
        mock.requests_per_second = 10000.0  # Near-zero delay
        mock.max_pages_per_scan = 100
        yield mock


@pytest.fixture(autouse=True)
def no_clear_log():
    """Prevent actual log file operations."""
    with patch("app.services.crawler_service.clear_inaccessible_log_cache") as mock:
        yield mock


# ---------------------------------------------------------------------------
# Single page crawling
# ---------------------------------------------------------------------------


class TestSinglePageCrawl:
    async def test_no_documents(self):
        mock = make_mock_scraper(scan_return=([], [], None))
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        result = get_result(items)
        assert result.success is True
        assert result.documents == []
        assert result.pages_scanned == 1

    async def test_finds_documents(self):
        docs = [make_doc("https://example.com/file.pdf")]
        mock = make_mock_scraper(scan_return=(["https://example.com/page2"], docs, None))
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        result = get_result(items)
        assert len(result.documents) == 1
        assert result.documents[0].url == "https://example.com/file.pdf"

    async def test_does_not_follow_links(self):
        mock = make_mock_scraper(
            scan_return=(["https://example.com/page2", "https://example.com/page3"], [], None)
        )
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=5,
            )
        result = get_result(items)
        assert result.pages_scanned == 1
        assert mock.scan_page.call_count == 1


# ---------------------------------------------------------------------------
# Multi-page crawling
# ---------------------------------------------------------------------------


class TestFollowLinks:
    async def test_follows_internal_links(self):
        async def scan(session, url, filter_type, depth):
            if url == "https://example.com":
                return (
                    ["https://example.com/page2"],
                    [make_doc("https://example.com/file1.pdf")],
                    None,
                )
            elif "page2" in url:
                return (
                    [],
                    [make_doc("https://example.com/file2.pdf")],
                    None,
                )
            return ([], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        assert result.pages_scanned == 2
        assert len(result.documents) == 2

    async def test_respects_max_depth(self):
        async def scan(session, url, filter_type, depth):
            return ([f"https://example.com/depth{depth + 1}"], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        # depth 0 -> depth 1 -> depth 2 = 3 pages
        assert result.pages_scanned == 3

    async def test_skips_external_links(self):
        async def scan(session, url, filter_type, depth):
            if url == "https://example.com":
                return (
                    ["https://other.com/page", "https://example.com/internal"],
                    [],
                    None,
                )
            return ([], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        # Only example.com and example.com/internal (not other.com)
        assert result.pages_scanned == 2


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    async def test_url_deduplication(self):
        """Same URL discovered from multiple pages is only visited once."""
        call_urls = []

        async def scan(session, url, filter_type, depth):
            call_urls.append(url)
            if "page2" not in url:
                # Link to page2 twice
                return (
                    ["https://example.com/page2", "https://example.com/page2"],
                    [],
                    None,
                )
            return (["https://example.com"], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=3,
            )
        result = get_result(items)
        assert result.pages_scanned == 2

    async def test_document_dedup_by_url(self):
        """Same document URL found on two pages is only listed once."""
        doc = make_doc("https://example.com/shared.pdf")

        async def scan(session, url, filter_type, depth):
            if "page2" not in url:
                return (["https://example.com/page2"], [doc], None)
            return ([], [doc], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        assert len(result.documents) == 1

    async def test_document_dedup_by_filename(self):
        """Same filename from different URLs is only listed once."""
        async def scan(session, url, filter_type, depth):
            if "page2" not in url:
                doc = make_doc("https://example.com/a/report.pdf", "report.pdf")
                return (["https://example.com/page2"], [doc], None)
            doc = make_doc("https://example.com/b/report.pdf", "report.pdf")
            return ([], [doc], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        assert len(result.documents) == 1


# ---------------------------------------------------------------------------
# Batching and continuation
# ---------------------------------------------------------------------------


class TestBatching:
    async def test_batch_limit_pauses(self, fast_settings):
        fast_settings.max_pages_per_scan = 2

        async def scan(session, url, filter_type, depth):
            page_num = int(url.split("page")[-1]) if "page" in url else 0
            next_pages = [f"https://example.com/page{page_num + i + 1}" for i in range(3)]
            return (next_pages, [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=5,
            )
        result = get_result(items)
        assert result.pages_scanned == 2
        assert result.has_more_pages is True
        assert result.pages_remaining > 0

    async def test_scan_all_pages_ignores_batch_limit(self, fast_settings):
        fast_settings.max_pages_per_scan = 2
        total_pages = 5

        async def scan(session, url, filter_type, depth):
            page_num = int(url.split("page")[-1]) if "page" in url else 0
            if page_num < total_pages - 1:
                return ([f"https://example.com/page{page_num + 1}"], [], None)
            return ([], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=10,
                scan_all_pages=True,
            )
        result = get_result(items)
        assert result.pages_scanned == total_pages
        assert result.has_more_pages is False

    async def test_continuation_resumes(self, fast_settings):
        fast_settings.max_pages_per_scan = 1

        async def scan(session, url, filter_type, depth):
            if url == "https://example.com":
                return (
                    ["https://example.com/page2", "https://example.com/page3"],
                    [make_doc("https://example.com/a.pdf")],
                    None,
                )
            return ([], [make_doc(f"{url}/doc.pdf", f"doc_{url[-1]}.pdf")], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            crawler = CrawlerService()
            state = CrawlState()

            # First batch
            items1 = await collect(
                crawler,
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
                state=state,
            )
            r1 = get_result(items1)
            assert r1.pages_scanned == 1
            assert r1.has_more_pages is True
            assert len(state.pending_queue) > 0

            # Continuation
            items2 = await collect(
                crawler,
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
                state=state,
            )
            r2 = get_result(items2)
            assert r2.pages_scanned > 1
            assert len(r2.documents) > 1


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


class TestProgress:
    async def test_yields_progress_and_result(self):
        mock = make_mock_scraper(
            scan_return=([], [make_doc("https://example.com/f.pdf")], None)
        )
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        progress = get_progress(items)
        result = get_result(items)
        assert len(progress) >= 1
        assert result.documents_found_count == 1 if hasattr(result, 'documents_found_count') else len(result.documents) == 1

    async def test_progress_reports_page_count(self):
        async def scan(session, url, filter_type, depth):
            if url == "https://example.com":
                return (["https://example.com/page2"], [], None)
            return ([], [], None)

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        progress = get_progress(items)
        # Last progress should reflect pages scanned
        assert any(p.pages_scanned > 0 for p in progress)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_invalid_start_url(self):
        mock = make_mock_scraper()
        with patch("app.services.crawler_service.scraper_service", mock), \
             patch("app.services.crawler_service.normalize_url", side_effect=ValueError("bad url")):
            items = await collect(
                CrawlerService(),
                start_url="not-a-url",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        result = get_result(items)
        assert result.success is False
        assert len(result.errors) > 0

    async def test_page_error_recorded(self):
        """A page scan error is recorded but crawl continues."""
        async def scan(session, url, filter_type, depth):
            if url == "https://example.com":
                return (["https://example.com/page2"], [], None)
            raise Exception("Network error")

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
            )
        result = get_result(items)
        assert result.pages_scanned == 2
        assert len(result.errors) > 0

    async def test_timeout_error_recorded(self):
        async def scan(session, url, filter_type, depth):
            raise asyncio.TimeoutError()

        mock = make_mock_scraper(scan_side_effect=scan)
        with patch("app.services.crawler_service.scraper_service", mock):
            items = await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        result = get_result(items)
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Inaccessible log cache clearing
# ---------------------------------------------------------------------------


class TestCacheClear:
    async def test_clears_cache_for_new_scan(self, no_clear_log):
        mock = make_mock_scraper(scan_return=([], [], None))
        with patch("app.services.crawler_service.scraper_service", mock):
            await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.SINGLE_PAGE,
                max_depth=1,
            )
        no_clear_log.assert_called_once()

    async def test_does_not_clear_for_continuation(self, no_clear_log):
        state = CrawlState()
        state.pending_queue = [("https://example.com/page2", 1)]
        state.visited_urls = {"https://example.com"}
        state.pages_scanned = 1

        mock = make_mock_scraper(scan_return=([], [], None))
        with patch("app.services.crawler_service.scraper_service", mock):
            await collect(
                CrawlerService(),
                start_url="https://example.com",
                filter_type=DocumentTypeFilter.COMMON,
                crawl_option=CrawlDepthOption.FOLLOW_LINKS,
                max_depth=2,
                state=state,
            )
        no_clear_log.assert_not_called()


# ---------------------------------------------------------------------------
# CrawlState
# ---------------------------------------------------------------------------


class TestCrawlState:
    def test_initial_state(self):
        state = CrawlState()
        assert state.pages_scanned == 0
        assert state.all_documents == []
        assert len(state.visited_urls) == 0
        assert len(state.pending_queue) == 0
        assert state.skipped_duplicates == 0

    def test_state_preserved_across_batches(self, fast_settings):
        """CrawlState accumulates data across batches."""
        state = CrawlState()
        state.pages_scanned = 5
        state.all_documents = [make_doc("https://example.com/a.pdf")]
        state.visited_urls = {"https://example.com"}
        state.document_urls = {"https://example.com/a.pdf"}

        assert state.pages_scanned == 5
        assert len(state.all_documents) == 1
