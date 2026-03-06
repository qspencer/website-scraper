"""Tests for the browser service."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.browser_service as browser_mod


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level globals before each test."""
    browser_mod._browser = None
    browser_mod._playwright = None
    yield
    browser_mod._browser = None
    browser_mod._playwright = None


# ---------------------------------------------------------------------------
# get_browser
# ---------------------------------------------------------------------------


class TestGetBrowser:
    async def test_creates_browser_when_none(self):
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True

        mock_chromium = MagicMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium

        mock_pw_cm = AsyncMock()
        mock_pw_cm.start = AsyncMock(return_value=mock_pw)

        with patch("app.services.browser_service.async_playwright", return_value=mock_pw_cm):
            result = await browser_mod.get_browser()

        assert result is mock_browser
        mock_chromium.launch.assert_awaited_once()

    async def test_reuses_connected_browser(self):
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        browser_mod._browser = mock_browser
        browser_mod._playwright = MagicMock()

        result = await browser_mod.get_browser()
        assert result is mock_browser

    async def test_recreates_disconnected_browser(self):
        old_browser = MagicMock()
        old_browser.is_connected.return_value = False
        browser_mod._browser = old_browser

        new_browser = MagicMock()
        new_browser.is_connected.return_value = True

        mock_chromium = MagicMock()
        mock_chromium.launch = AsyncMock(return_value=new_browser)

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium

        mock_pw_cm = AsyncMock()
        mock_pw_cm.start = AsyncMock(return_value=mock_pw)

        with patch("app.services.browser_service.async_playwright", return_value=mock_pw_cm):
            result = await browser_mod.get_browser()

        assert result is new_browser


# ---------------------------------------------------------------------------
# close_browser
# ---------------------------------------------------------------------------


class TestCloseBrowser:
    async def test_closes_browser_and_playwright(self):
        mock_browser = MagicMock()
        mock_browser.close = AsyncMock()
        mock_pw = MagicMock()
        mock_pw.stop = AsyncMock()

        browser_mod._browser = mock_browser
        browser_mod._playwright = mock_pw

        await browser_mod.close_browser()

        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert browser_mod._browser is None
        assert browser_mod._playwright is None

    async def test_close_when_nothing_open(self):
        # Should not raise
        await browser_mod.close_browser()
        assert browser_mod._browser is None

    async def test_close_browser_only(self):
        mock_browser = MagicMock()
        mock_browser.close = AsyncMock()
        browser_mod._browser = mock_browser
        # No playwright set

        await browser_mod.close_browser()
        mock_browser.close.assert_awaited_once()
        assert browser_mod._browser is None


# ---------------------------------------------------------------------------
# fetch_page_with_browser
# ---------------------------------------------------------------------------


class TestFetchPageWithBrowser:
    async def test_success(self):
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html><body>Rendered</body></html>")

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch("app.services.browser_service.get_browser", new=AsyncMock(return_value=mock_browser)):
            with patch("asyncio.sleep", new=AsyncMock()):
                html, error = await browser_mod.fetch_page_with_browser("https://example.com")

        assert html == "<html><body>Rendered</body></html>"
        assert error is None
        mock_page.goto.assert_awaited_once()
        mock_context.close.assert_awaited_once()

    async def test_network_idle_timeout_is_ok(self):
        """Network idle timeout should not fail the fetch."""
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock(side_effect=Exception("Timeout"))
        mock_page.content = AsyncMock(return_value="<html>OK</html>")

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch("app.services.browser_service.get_browser", new=AsyncMock(return_value=mock_browser)):
            with patch("asyncio.sleep", new=AsyncMock()):
                html, error = await browser_mod.fetch_page_with_browser("https://example.com")

        assert html == "<html>OK</html>"
        assert error is None

    async def test_navigation_error(self):
        mock_page = MagicMock()
        mock_page.goto = AsyncMock(side_effect=Exception("net::ERR_NAME_NOT_RESOLVED"))

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch("app.services.browser_service.get_browser", new=AsyncMock(return_value=mock_browser)):
            with patch("asyncio.sleep", new=AsyncMock()):
                html, error = await browser_mod.fetch_page_with_browser("https://nonexistent.example")

        assert html is None
        assert "Browser error" in error
        # Context should still be closed (finally block)
        mock_context.close.assert_awaited_once()

    async def test_get_browser_failure(self):
        with patch("app.services.browser_service.get_browser",
                   new=AsyncMock(side_effect=Exception("Failed to launch"))):
            html, error = await browser_mod.fetch_page_with_browser("https://example.com")

        assert html is None
        assert "Browser error" in error
