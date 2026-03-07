"""Service for extracting text from various document formats."""

import io
from typing import Tuple

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def extract_text(file_data: bytes, extension: str) -> Tuple[str, str]:
    """
    Extract text from a document.

    Args:
        file_data: Raw file bytes
        extension: File extension (e.g. ".pdf", ".docx")

    Returns:
        Tuple of (extracted_text, status) where status is
        "complete", "failed", or "unsupported"
    """
    ext = extension.lower().lstrip(".")

    extractors = {
        "pdf": _extract_pdf,
        "docx": _extract_docx,
        "doc": _extract_docx,  # python-docx can sometimes handle .doc
        "xlsx": _extract_xlsx,
        "xls": _extract_xlsx,
        "txt": _extract_text_file,
        "csv": _extract_text_file,
        "md": _extract_text_file,
        "json": _extract_text_file,
        "xml": _extract_text_file,
        "html": _extract_text_file,
        "htm": _extract_text_file,
    }

    extractor = extractors.get(ext)
    if not extractor:
        logger.debug(f"No text extractor for .{ext}")
        return "", "unsupported"

    try:
        text = extractor(file_data)
        if text and text.strip():
            logger.debug(f"Extracted {len(text)} chars from .{ext} file")
            return text.strip(), "complete"
        else:
            logger.debug(f"No text content extracted from .{ext} file")
            return "", "failed"
    except Exception as e:
        logger.warning(f"Text extraction failed for .{ext}: {e}")
        return "", "failed"


def _extract_pdf(file_data: bytes) -> str:
    """Extract text from a PDF file."""
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(file_data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_docx(file_data: bytes) -> str:
    """Extract text from a DOCX file."""
    from docx import Document

    doc = Document(io.BytesIO(file_data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_xlsx(file_data: bytes) -> str:
    """Extract text from an XLSX file."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(file_data), read_only=True, data_only=True)
    parts = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        parts.append(f"[Sheet: {sheet}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


def _extract_text_file(file_data: bytes) -> str:
    """Extract text from a plain text file."""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return file_data.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return file_data.decode("utf-8", errors="replace")
