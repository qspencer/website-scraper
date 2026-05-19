"""Live AI smoke test — one real billable call to the configured AI endpoint.

Verifies the production response-shape parser. The 52 mocked tests in
test_ai_summarization.py cover retry, backoff, and provider detection logic; this
catches the case where the live provider's response shape drifts from what the
mocks asserted (e.g. OpenAI's max_tokens → max_completion_tokens migration that
this evaluation already caught at pre-flight).
"""

import pytest

from app.services import ai_summarization_service
from app.services.settings_service import runtime_settings

pytestmark = [pytest.mark.integration, pytest.mark.live_ai]


_FIXTURE_TEXT = (
    "Acme Quarterly Operations Report — Q1 2026. Production capacity grew 14% "
    "year-over-year. Three new production lines came online in February. "
    "Workforce expanded by 22 net hires; voluntary attrition remained below 4%. "
    "Capital expenditure of $4.2M tracked under the FY26 plan with no material "
    "variances. Outlook for Q2 expects continued growth with one new product launch."
)


@pytest.fixture(scope="module", autouse=True)
def _ai_configured():
    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        pytest.skip("AI API not configured (set ai_api_url, ai_api_key, ai_model in settings)")


async def test_ai_summarizes_fixture_document():
    result = await ai_summarization_service._call_ai_api(
        filename="acme-q1-2026.txt",
        text=_FIXTURE_TEXT,
    )

    assert "error" not in result, f"Live AI call returned error: {result.get('error')}"
    # Contract the rest of the app depends on: these keys must be present and non-empty.
    for key in ("title", "short_summary", "summary", "keywords", "document_type"):
        assert key in result, f"Live AI response missing key: {key!r}"
    assert isinstance(result["title"], str) and result["title"].strip()
    assert isinstance(result["short_summary"], str) and result["short_summary"].strip()
    assert isinstance(result["keywords"], list) and len(result["keywords"]) >= 1
