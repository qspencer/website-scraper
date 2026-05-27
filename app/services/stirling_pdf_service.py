"""Service for PDF text extraction and OCR processing via Stirling PDF API."""

import io
from typing import Optional

import requests

from app.core.logging_config import get_logger
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)

# Timeouts
TEXT_EXTRACT_TIMEOUT = 30  # seconds - fast text extraction
OCR_TIMEOUT = 1800  # 30 minutes - OCR on large/complex scanned PDFs can hit the prior 10min cap
OFFICE_CONVERT_TIMEOUT = 120  # 2 minutes - office-to-PDF via LibreOffice headless


def is_configured() -> bool:
    """Check if Stirling PDF is configured and reachable."""
    url = runtime_settings.stirling_pdf_url
    if not url:
        return False
    try:
        resp = requests.get(f"{url}/api/v1/info/status", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def extract_text_direct(file_data: bytes) -> Optional[str]:
    """Extract text from a PDF using Stirling's PDF-to-text conversion.

    This is fast but only works for PDFs that already have a text layer.
    Scanned/image-only PDFs will return empty text.

    Returns extracted text, or None on failure.
    """
    url = runtime_settings.stirling_pdf_url
    if not url:
        return None

    endpoint = f"{url}/api/v1/convert/pdf/text"

    try:
        resp = requests.post(
            endpoint,
            files={"fileInput": ("document.pdf", io.BytesIO(file_data), "application/pdf")},
            data={"outputFormat": "txt"},
            timeout=TEXT_EXTRACT_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.warning(
                f"Stirling PDF text extract returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return None

        text = resp.text.strip()
        if text:
            logger.debug(f"Stirling PDF text extract got {len(text)} chars")
            return text
        return None

    except requests.Timeout:
        logger.warning("Stirling PDF text extract timed out")
        return None
    except requests.ConnectionError as e:
        logger.warning(f"Cannot connect to Stirling PDF at {url}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Stirling PDF text extract failed: {e}")
        return None


def ocr_pdf(file_data: bytes) -> Optional[bytes]:
    """Run OCR on a PDF file and return the searchable PDF bytes.

    Sends the PDF to Stirling PDF's OCR endpoint with force-ocr mode,
    which processes all pages regardless of existing text layers.

    Returns the OCR'd PDF bytes, or None on failure.
    """
    url = runtime_settings.stirling_pdf_url
    if not url:
        logger.debug("Stirling PDF URL not configured")
        return None

    endpoint = f"{url}/api/v1/misc/ocr-pdf"

    try:
        resp = requests.post(
            endpoint,
            files={"fileInput": ("document.pdf", io.BytesIO(file_data), "application/pdf")},
            data={
                "languages": "eng",
                "ocrType": "force-ocr",
                "ocrRenderType": "hocr",
            },
            timeout=OCR_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.warning(
                f"Stirling PDF OCR returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return None

        result = resp.content
        if len(result) < 100:
            logger.warning("Stirling PDF OCR returned unexpectedly small output")
            return None

        logger.debug(f"Stirling PDF OCR produced {len(result)} bytes")
        return result

    except requests.Timeout:
        logger.error("Stirling PDF OCR request timed out")
        return None
    except requests.ConnectionError as e:
        logger.error(f"Cannot connect to Stirling PDF at {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Stirling PDF OCR failed: {e}")
        return None


def extract_text_via_ocr(file_data: bytes) -> Optional[str]:
    """OCR a PDF and extract text from the result.

    Sends the PDF through Stirling PDF OCR, then extracts text
    from the resulting searchable PDF using PyPDF2.

    Returns extracted text, or None on failure.
    """
    ocr_result = ocr_pdf(file_data)
    if not ocr_result:
        return None

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(ocr_result))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        full_text = "\n\n".join(pages).strip()

        if full_text:
            logger.debug(f"OCR text extraction got {len(full_text)} chars from {len(reader.pages)} pages")
            return full_text
        else:
            logger.warning("OCR produced a PDF but no text could be extracted from it")
            return None

    except Exception as e:
        logger.error(f"Failed to extract text from OCR'd PDF: {e}")
        return None


def convert_to_pdf(file_data: bytes, filename: str) -> Optional[bytes]:
    """Convert an office document (e.g. legacy .doc) to PDF via Stirling-PDF.

    Stirling uses LibreOffice headless under the hood, so it handles the binary .doc
    format that python-docx cannot read. Returns the PDF bytes, or None on any failure
    (Stirling unavailable, conversion failed, unsupported source format).

    The returned PDF can then be fed through the normal PDF text-extraction chain
    (PyPDF2 → Stirling OCR fallback) to recover the text.
    """
    url = runtime_settings.stirling_pdf_url
    if not url:
        return None

    endpoint = f"{url}/api/v1/convert/file/pdf"

    try:
        resp = requests.post(
            endpoint,
            files={"fileInput": (filename, io.BytesIO(file_data), "application/octet-stream")},
            timeout=OFFICE_CONVERT_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.warning(
                f"Stirling office-to-PDF conversion returned HTTP {resp.status_code} "
                f"for {filename}: {resp.text[:200]}"
            )
            return None

        result = resp.content
        if not result.startswith(b"%PDF"):
            logger.warning(f"Stirling office-to-PDF returned non-PDF output for {filename}")
            return None

        logger.debug(f"Converted {filename} to PDF: {len(file_data)} -> {len(result)} bytes")
        return result

    except requests.Timeout:
        logger.error(f"Stirling office-to-PDF conversion timed out for {filename}")
        return None
    except requests.ConnectionError as e:
        logger.error(f"Cannot connect to Stirling PDF at {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Stirling office-to-PDF conversion failed for {filename}: {e}")
        return None


def extract_text_with_fallback(file_data: bytes) -> Optional[str]:
    """Try fast text extraction first, then fall back to OCR.

    1. Try Stirling's direct PDF-to-text (fast, ~1 second)
    2. If that returns nothing, try full OCR (slow, 1-5 minutes)

    Returns extracted text, or None if both methods fail.
    """
    # Fast path: direct text extraction
    text = extract_text_direct(file_data)
    if text:
        logger.info("Stirling PDF: text extracted via fast path")
        return text

    # Slow path: OCR
    logger.info("Stirling PDF: fast path empty, falling back to OCR")
    return extract_text_via_ocr(file_data)
