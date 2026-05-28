"""Stirling-PDF integration smoke — closes the 0% coverage gap on stirling_pdf_service.

Builds a minimal valid PDF in-process (see conftest.build_minimal_pdf), POSTs it
through the configured Stirling endpoint, and asserts the marker string round-trips.
This catches regressions caused by Stirling API/version changes or by the container
being unreachable.
"""

import os
import shutil
import subprocess
import tempfile

import pytest

from app.services import stirling_pdf_service
from app.services.text_extraction_service import extract_text
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


def test_extract_real_legacy_doc(_stirling_reachable):
    """R-TEST-6: decode a genuine legacy OLE .doc end-to-end (Stirling convert → PyPDF2).

    Generates a real .doc with LibreOffice headless (the same engine Stirling uses),
    confirms it's an OLE compound file, then runs it through extract_text — exercising
    _extract_doc → stirling convert_to_pdf → _extract_pdf, not a mock.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        pytest.skip("LibreOffice not available to generate a .doc fixture")

    marker = "Railcar Reporting Marks Application"
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.txt")
        with open(src, "w") as f:
            f.write(marker + "\n\nSection 1: applicant details.\n")
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "doc", "--outdir", d, src],
            capture_output=True, timeout=90,
        )
        doc_path = os.path.join(d, "src.doc")
        if proc.returncode != 0 or not os.path.exists(doc_path):
            pytest.skip(f"LibreOffice could not produce a .doc: {proc.stderr.decode()[:200]}")
        with open(doc_path, "rb") as f:
            doc_bytes = f.read()

    # Genuine legacy .doc is an OLE compound document.
    assert doc_bytes[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "fixture is not a legacy OLE .doc"

    text, status, error = extract_text(doc_bytes, ".doc")
    assert status == "complete", f"expected complete, got {status!r}: {error}"
    assert "Reporting Marks" in text, f"marker missing from extracted text: {text[:200]!r}"
