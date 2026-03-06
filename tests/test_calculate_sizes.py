"""Tests for ScraperService.calculate_file_sizes."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.scraper_service import ScraperService


class TestCalculateFileSizes:
    async def test_returns_sizes_for_all_urls(self):
        scraper = ScraperService()
        urls = [
            "https://example.com/a.pdf",
            "https://example.com/b.pdf",
        ]

        async def mock_get_size(session, url):
            sizes = {
                "https://example.com/a.pdf": 1000,
                "https://example.com/b.pdf": 2000,
            }
            return sizes.get(url)

        with patch.object(scraper, "get_file_size_via_get", side_effect=mock_get_size):
            results = await scraper.calculate_file_sizes(urls)

        assert results["https://example.com/a.pdf"] == 1000
        assert results["https://example.com/b.pdf"] == 2000

    async def test_handles_none_sizes(self):
        scraper = ScraperService()
        urls = ["https://example.com/unknown.pdf"]

        with patch.object(scraper, "get_file_size_via_get", new=AsyncMock(return_value=None)):
            results = await scraper.calculate_file_sizes(urls)

        assert results["https://example.com/unknown.pdf"] is None

    async def test_handles_exceptions_gracefully(self):
        scraper = ScraperService()
        urls = [
            "https://example.com/good.pdf",
            "https://example.com/error.pdf",
        ]

        call_count = 0

        async def mock_get_size(session, url):
            nonlocal call_count
            call_count += 1
            if "error" in url:
                raise Exception("Connection failed")
            return 5000

        with patch.object(scraper, "get_file_size_via_get", side_effect=mock_get_size):
            results = await scraper.calculate_file_sizes(urls)

        # good.pdf should be in results, error.pdf may or may not depending on exception handling
        assert "https://example.com/good.pdf" in results
        assert results["https://example.com/good.pdf"] == 5000

    async def test_empty_urls(self):
        scraper = ScraperService()

        with patch.object(scraper, "get_file_size_via_get", new=AsyncMock(return_value=100)):
            results = await scraper.calculate_file_sizes([])

        assert results == {}

    async def test_respects_concurrency_limit(self):
        scraper = ScraperService()
        urls = [f"https://example.com/file{i}.pdf" for i in range(20)]
        max_concurrent = 3

        active = 0
        max_active = 0

        original_get_size = AsyncMock(return_value=100)

        async def tracking_get_size(session, url):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return 100

        with patch.object(scraper, "get_file_size_via_get", side_effect=tracking_get_size):
            results = await scraper.calculate_file_sizes(urls, max_concurrent=max_concurrent)

        assert len(results) == 20
        assert max_active <= max_concurrent
