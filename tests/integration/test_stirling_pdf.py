"""Stirling-PDF integration smoke — closes the 0% coverage gap on stirling_pdf_service.

Builds a minimal valid PDF in-process (see conftest.build_minimal_pdf), POSTs it
through the configured Stirling endpoint, and asserts the marker string round-trips.
This catches regressions caused by Stirling API/version changes or by the container
being unreachable.
"""

import pytest

from app.services import stirling_pdf_service
from tests.integration.conftest import build_minimal_pdf

pytestmark = pytest.mark.integration

_MARKER = "Wave4StirlingMarker"


@pytest.fixture(scope="module", autouse=True)
def _stirling_reachable():
    if not stirling_pdf_service.is_configured():
        pytest.skip("Stirling-PDF not reachable at configured URL")


def test_extract_text_direct_round_trips_marker():
    pdf_bytes = build_minimal_pdf(_MARKER)
    assert pdf_bytes.startswith(b"%PDF-"), "fixture builder produced invalid PDF"

    extracted = stirling_pdf_service.extract_text_direct(pdf_bytes)
    assert extracted is not None, "Stirling returned None — service may be down or PDF malformed"
    assert _MARKER in extracted, f"Marker {_MARKER!r} not found in extracted text: {extracted!r}"


def test_is_configured_returns_true_when_live(_stirling_reachable):
    # Belt-and-suspenders sanity — if the autouse fixture didn't skip, is_configured() must agree.
    assert stirling_pdf_service.is_configured() is True
