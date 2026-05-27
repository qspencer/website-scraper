"""Service for extracting text from various document formats."""

import io
from typing import Tuple

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def extract_text(file_data: bytes, extension: str) -> Tuple[str, str, str]:
    """
    Extract text from a document.

    Args:
        file_data: Raw file bytes
        extension: File extension (e.g. ".pdf", ".docx")

    Returns:
        Tuple of (extracted_text, status, error) where status is
        "complete", "failed", or "unsupported", and error is a
        human-readable reason on failure (empty string on success).
    """
    ext = extension.lower().lstrip(".")

    extractors = {
        "pdf": _extract_pdf,
        "docx": _extract_docx,
        "doc": _extract_doc,  # legacy binary .doc — needs Stirling-PDF (LibreOffice) conversion
        "pptx": _extract_pptx,
        "ppt": _extract_pptx,
        "xlsx": _extract_xlsx,
        "xls": _extract_xls,
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
        return "", "unsupported", f"No text extractor for .{ext} files"

    try:
        text = extractor(file_data)
        if text and text.strip():
            logger.debug(f"Extracted {len(text)} chars from .{ext} file")
            return text.strip(), "complete", ""
        else:
            logger.debug(f"No text content extracted from .{ext} file")
            return "", "failed", f"No text content found in .{ext} file"
    except Exception as e:
        logger.warning(f"Text extraction failed for .{ext}: {e}")
        return "", "failed", f"Extraction error: {e}"


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


def _extract_doc(file_data: bytes) -> str:
    """Extract text from a legacy binary .doc file.

    python-docx only handles Office Open XML (.docx), not the pre-2007 binary .doc
    format. We convert .doc → PDF via Stirling-PDF (which uses LibreOffice headless),
    then run the resulting PDF through the normal PDF text extractor. Raises a clear
    RuntimeError when Stirling is unavailable so the dispatcher's error message
    surfaces the real cause instead of "No text content found".
    """
    from app.services import stirling_pdf_service

    if not stirling_pdf_service.is_configured():
        raise RuntimeError(
            "Cannot extract legacy .doc files without Stirling-PDF "
            "(LibreOffice). Ensure the Stirling-PDF container is running "
            "and stirling_pdf_url is set in Settings."
        )

    pdf_bytes = stirling_pdf_service.convert_to_pdf(file_data, "input.doc")
    if not pdf_bytes:
        raise RuntimeError("Stirling-PDF failed to convert .doc to PDF")

    return _extract_pdf(pdf_bytes)


def _extract_pptx(file_data: bytes) -> str:
    """Extract text from a PowerPoint file."""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(file_data))
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        texts.append(text)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        texts.append(" | ".join(cells))
        if texts:
            slides.append(f"[Slide {i}]\n" + "\n".join(texts))
    return "\n\n".join(slides)


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


def _extract_xls(file_data: bytes) -> str:
    """Extract text from a legacy .xls file using xlrd."""
    import xlrd

    wb = xlrd.open_workbook(file_contents=file_data)
    parts = []
    for sheet in wb.sheets():
        parts.append(f"[Sheet: {sheet.name}]")
        for row_idx in range(sheet.nrows):
            cells = [str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols)
                     if sheet.cell_value(row_idx, col) not in (None, "")]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_spreadsheet_metadata(file_data: bytes, extension: str) -> str:
    """Extract structured metadata from a spreadsheet for AI summarization.

    Returns a formatted string describing sheet names, column headers,
    row counts, and sample data rows for each sheet.
    """
    ext = extension.lower().lstrip(".")

    if ext in ("csv", "tsv"):
        return _extract_csv_metadata(file_data, ext)

    if ext == "xls":
        return _extract_xls_metadata(file_data)

    # xlsx and other openpyxl-supported formats
    return _extract_xlsx_metadata(file_data)


def _extract_xlsx_metadata(file_data: bytes) -> str:
    """Extract metadata from an .xlsx file using openpyxl."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(file_data), read_only=True, data_only=True)
    except Exception as e:
        logger.warning(f"Failed to open xlsx for metadata: {e}")
        return ""

    parts = [f"Spreadsheet with {len(wb.sheetnames)} sheet(s): {', '.join(wb.sheetnames)}"]

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c for c in cells):
                rows.append(cells)

        row_count = len(rows)
        parts.append(f"\n--- Sheet: {sheet_name} ({row_count} rows) ---")

        if row_count == 0:
            parts.append("  (empty sheet)")
            continue

        headers = rows[0]
        parts.append(f"  Column headers: {' | '.join(headers)}")

        sample_rows = rows[1:6]
        if sample_rows:
            parts.append(f"  Sample data ({min(len(sample_rows), 5)} of {row_count - 1} data rows):")
            for row in sample_rows:
                parts.append(f"    {' | '.join(row)}")

        if row_count > 6:
            parts.append(f"  ... and {row_count - 6} more rows")

    wb.close()
    return "\n".join(parts)


def _extract_xls_metadata(file_data: bytes) -> str:
    """Extract metadata from a legacy .xls file using xlrd."""
    import xlrd

    try:
        wb = xlrd.open_workbook(file_contents=file_data)
    except Exception as e:
        logger.warning(f"Failed to open xls for metadata: {e}")
        return ""

    sheet_names = wb.sheet_names()
    parts = [f"Spreadsheet with {len(sheet_names)} sheet(s): {', '.join(sheet_names)}"]

    for sheet in wb.sheets():
        rows = []
        for row_idx in range(sheet.nrows):
            cells = [str(sheet.cell_value(row_idx, col)) if sheet.cell_value(row_idx, col) not in (None, "")
                     else "" for col in range(sheet.ncols)]
            if any(c for c in cells):
                rows.append(cells)

        row_count = len(rows)
        parts.append(f"\n--- Sheet: {sheet.name} ({row_count} rows) ---")

        if row_count == 0:
            parts.append("  (empty sheet)")
            continue

        headers = rows[0]
        parts.append(f"  Column headers: {' | '.join(headers)}")

        sample_rows = rows[1:6]
        if sample_rows:
            parts.append(f"  Sample data ({min(len(sample_rows), 5)} of {row_count - 1} data rows):")
            for row in sample_rows:
                parts.append(f"    {' | '.join(row)}")

        if row_count > 6:
            parts.append(f"  ... and {row_count - 6} more rows")

    return "\n".join(parts)


def _extract_csv_metadata(file_data: bytes, ext: str) -> str:
    """Extract metadata from a CSV/TSV file."""
    import csv

    # Decode the file
    text = ""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            text = file_data.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if not text:
        return ""

    delimiter = "\t" if ext == "tsv" else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    rows = []
    for i, row in enumerate(reader):
        if i >= 50:  # read enough for metadata
            break
        if any(c.strip() for c in row):
            rows.append(row)

    if not rows:
        return ""

    total_lines = text.count("\n")
    parts = [f"CSV file with approximately {total_lines} rows"]

    headers = rows[0]
    parts.append(f"Column headers: {' | '.join(headers)}")

    sample_rows = rows[1:6]
    if sample_rows:
        parts.append(f"Sample data ({min(len(sample_rows), 5)} rows):")
        for row in sample_rows:
            parts.append(f"  {' | '.join(row)}")

    if total_lines > 6:
        parts.append(f"... and approximately {total_lines - 6} more rows")

    return "\n".join(parts)


def _extract_text_file(file_data: bytes) -> str:
    """Extract text from a plain text file."""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return file_data.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return file_data.decode("utf-8", errors="replace")
