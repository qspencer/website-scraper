import asyncio
from typing import Optional, Tuple
from playwright.async_api import async_playwright, Browser, Page

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Global browser instance (reused across requests)
_browser: Optional[Browser] = None
_playwright = None


async def get_browser() -> Browser:
    """Get or create a shared browser instance."""
    global _browser, _playwright

    if _browser is None or not _browser.is_connected():
        logger.info("Launching headless browser...")
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--no-sandbox",
            ]
        )
        logger.info("Headless browser launched")

    return _browser


async def close_browser():
    """Close the shared browser instance."""
    global _browser, _playwright

    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    logger.info("Browser closed")


async def fetch_page_with_browser(url: str, wait_time: int = 3000) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch a page using a headless browser, waiting for JavaScript to execute.

    Args:
        url: The URL to fetch
        wait_time: Time to wait for JavaScript in milliseconds (default 3 seconds)

    Returns:
        Tuple of (html_content, error_message)
    """
    logger.info(f"Fetching with browser: {url}")

    try:
        browser = await get_browser()
        context = await browser.new_context(
            user_agent=settings.USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )

        page = await context.new_page()

        try:
            # Navigate to the page
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for network to be idle (no requests for 500ms)
            # This helps ensure JavaScript has finished loading content
            try:
                await page.wait_for_load_state("networkidle", timeout=wait_time)
            except Exception:
                # Network idle timeout is ok - some pages have continuous requests
                pass

            # Additional wait for any remaining JavaScript
            await asyncio.sleep(1)

            # Get the final rendered HTML
            html = await page.content()

            logger.info(f"Browser fetch complete: {url} ({len(html)} bytes)")
            return html, None

        finally:
            await context.close()

    except Exception as e:
        error_msg = str(e)[:100]
        logger.error(f"Browser fetch failed for {url}: {error_msg}")
        return None, f"Browser error: {error_msg}"
