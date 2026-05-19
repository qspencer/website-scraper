"""Real Playwright Chromium launch — closes the gap that test_browser_service.py
exercises only mocked APIs. Verifies that a real headless Chromium can be launched
and a page navigated end-to-end. Skips cleanly if Chromium isn't installed.
"""

import pytest

from app.services import browser_service

pytestmark = [pytest.mark.integration, pytest.mark.playwright]


_FIXTURE_HTML = (
    "data:text/html,"
    "<html><head><title>Wave4BrowserTest</title></head>"
    "<body><h1>Wave4BrowserMarker</h1><a href='/doc.pdf'>PDF Link</a></body></html>"
)


def _is_chromium_missing_error(error: str) -> bool:
    """Recognize the various ways Playwright signals that the browser isn't installed."""
    if not error:
        return False
    low = error.lower()
    return (
        "playwright install" in low
        or "executable doesn't exist" in low
        or "executable not found" in low
        or "browser not installed" in low
    )


async def test_real_chromium_renders_data_url():
    try:
        html, error = await browser_service.fetch_page_with_browser(_FIXTURE_HTML, wait_time=500)
        if _is_chromium_missing_error(error or ""):
            pytest.skip(f"Playwright Chromium not installed: {error}")
        assert error is None, f"Browser fetch returned error: {error}"
        assert html is not None
        assert "Wave4BrowserMarker" in html, f"Marker missing; got: {html[:300]!r}"
        assert "/doc.pdf" in html, "Link href should survive rendering"
    finally:
        # Tear down the module-level browser singleton so the test session can exit cleanly.
        try:
            await browser_service.close_browser()
        except Exception:
            pass
