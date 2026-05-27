"""Tests for the text extraction service."""

import pytest
from unittest.mock import patch, MagicMock
from app.services.text_extraction_service import extract_text, extract_spreadsheet_metadata


class TestExtractText:
    def test_unsupported_extension(self):
        text, status, *_ = extract_text(b"data", ".zip")
        assert text == ""
        assert status == "unsupported"

    def test_unsupported_extension_unknown(self):
        text, status, *_ = extract_text(b"data", ".exe")
        assert text == ""
        assert status == "unsupported"

    def test_plain_text_utf8(self):
        content = "Hello, world! This is a test."
        text, status, *_ = extract_text(content.encode("utf-8"), ".txt")
        assert status == "complete"
        assert text == content

    def test_plain_text_csv(self):
        content = "name,age\nAlice,30\nBob,25"
        text, status, *_ = extract_text(content.encode("utf-8"), ".csv")
        assert status == "complete"
        assert "Alice" in text

    def test_plain_text_md(self):
        content = "# Heading\n\nParagraph text."
        text, status, *_ = extract_text(content.encode("utf-8"), ".md")
        assert status == "complete"
        assert "Heading" in text

    def test_plain_text_json(self):
        content = '{"key": "value"}'
        text, status, *_ = extract_text(content.encode("utf-8"), ".json")
        assert status == "complete"
        assert "key" in text

    def test_plain_text_xml(self):
        content = "<root><item>test</item></root>"
        text, status, *_ = extract_text(content.encode("utf-8"), ".xml")
        assert status == "complete"
        assert "test" in text

    def test_plain_text_html(self):
        content = "<html><body>Hello</body></html>"
        text, status, *_ = extract_text(content.encode("utf-8"), ".html")
        assert status == "complete"
        assert "Hello" in text

    def test_plain_text_htm(self):
        content = "<html><body>Hello</body></html>"
        text, status, *_ = extract_text(content.encode("utf-8"), ".htm")
        assert status == "complete"

    def test_plain_text_latin1(self):
        content = "Héllo wörld"
        text, status, *_ = extract_text(content.encode("latin-1"), ".txt")
        assert status == "complete"
        assert "wörld" in text or "rld" in text

    def test_empty_text_file(self):
        text, status, *_ = extract_text(b"", ".txt")
        assert status == "failed"
        assert text == ""

    def test_whitespace_only_text_file(self):
        text, status, *_ = extract_text(b"   \n\n  ", ".txt")
        assert status == "failed"
        assert text == ""

    def test_extension_case_insensitive(self):
        content = "test content"
        text, status, *_ = extract_text(content.encode("utf-8"), ".TXT")
        assert status == "complete"
        assert text == content

    def test_extension_with_leading_dot(self):
        content = "test content"
        text, status, *_ = extract_text(content.encode("utf-8"), ".txt")
        assert status == "complete"

    def test_extension_without_leading_dot(self):
        """The service strips leading dots, so 'txt' should also work."""
        content = "test content"
        text, status, *_ = extract_text(content.encode("utf-8"), "txt")
        assert status == "complete"

    @patch("app.services.text_extraction_service._extract_pdf")
    def test_pdf_extraction(self, mock_pdf):
        mock_pdf.return_value = "PDF content here"
        text, status, *_ = extract_text(b"fake pdf data", ".pdf")
        assert status == "complete"
        assert text == "PDF content here"
        mock_pdf.assert_called_once_with(b"fake pdf data")

    @patch("app.services.text_extraction_service._extract_pdf")
    def test_pdf_extraction_empty(self, mock_pdf):
        mock_pdf.return_value = ""
        text, status, *_ = extract_text(b"fake pdf data", ".pdf")
        assert status == "failed"

    @patch("app.services.text_extraction_service._extract_pdf")
    def test_pdf_extraction_error(self, mock_pdf):
        mock_pdf.side_effect = Exception("parse error")
        text, status, *_ = extract_text(b"bad data", ".pdf")
        assert status == "failed"
        assert text == ""

    def test_pdf_validation_rejects_missing_magic_bytes(self):
        # Bytes don't start with %PDF- — could be an HTML error page mislabeled as .pdf
        text, status, error = extract_text(b"<html>404 Not Found</html>" + b" " * 1024, ".pdf")
        assert status == "corrupt"
        assert "%PDF- magic" in error
        assert text == ""

    def test_pdf_validation_rejects_missing_eof_marker(self):
        # Valid magic header but no %%EOF trailer — classic truncated download
        truncated = b"%PDF-1.4\n%binary marker\n" + b"x" * 2048
        text, status, error = extract_text(truncated, ".pdf")
        assert status == "corrupt"
        assert "%%EOF" in error
        assert "truncated" in error.lower()

    def test_pdf_validation_rejects_too_small(self):
        text, status, error = extract_text(b"%PDF-1.4", ".pdf")
        assert status == "corrupt"
        assert "too small" in error.lower()

    def test_pdf_validation_passes_for_minimal_valid_pdf(self):
        # Minimal structure that passes the cheap pre-check (PyPDF2 will then try to parse;
        # since it's not really a parseable PDF body, expect a real PyPDF2 error path).
        from app.services.text_extraction_service import _validate_pdf_structure
        body = b"%PDF-1.4\n" + b"placeholder body" * 100 + b"\n%%EOF\n"
        assert _validate_pdf_structure(body) is None


class TestFormatDetection:
    """Tests for _detect_actual_format — magic-byte recognition for misnamed files."""

    def test_detects_html(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b"<!DOCTYPE html><html><body>x</body></html>") == ".html"
        assert _detect_actual_format(b"  \n<html lang='en'>x</html>") == ".html"
        assert _detect_actual_format(b"<HTML>x</HTML>") == ".html"

    def test_detects_xml(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b'<?xml version="1.0"?><a/>') == ".xml"
        assert _detect_actual_format(b'<svg xmlns="...">') == ".xml"

    def test_detects_pdf(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b"%PDF-1.4\n...") == ".pdf"

    def test_detects_image_formats(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b"\x89PNG\r\n\x1a\n....") == ".png"
        assert _detect_actual_format(b"\xFF\xD8\xFF\xE0....") == ".jpg"
        assert _detect_actual_format(b"GIF87a....") == ".gif"
        # WEBP needs RIFF header + WEBP marker at offset 8
        assert _detect_actual_format(b"RIFF\x00\x00\x00\x00WEBPxxxx") == ".webp"

    def test_detects_plain_text(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b"hello world\nthis is plain text\n") == ".txt"

    def test_detects_legacy_ole_compound(self):
        from app.services.text_extraction_service import _detect_actual_format
        # OLE compound magic (legacy .doc/.xls/.ppt)
        assert _detect_actual_format(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 100) == ".doc"

    def test_returns_none_for_unknown_binary(self):
        from app.services.text_extraction_service import _detect_actual_format
        # NUL bytes + non-magic-matching → unrecognized
        assert _detect_actual_format(b"\x00\x01\x02\x03" * 100) is None

    def test_returns_none_for_empty(self):
        from app.services.text_extraction_service import _detect_actual_format
        assert _detect_actual_format(b"") is None


class TestExtractTextWithDetection:
    """Tests for the auto-rerouting wrapper."""

    def test_complete_extraction_passes_through_unchanged(self):
        from app.services.text_extraction_service import extract_text_with_detection
        # A real .txt file with the correct extension — no detection should fire
        text, status, error, actual_ext = extract_text_with_detection(b"hello world", ".txt")
        assert status == "complete"
        assert text == "hello world"
        assert actual_ext == ".txt"

    def test_html_misnamed_as_pdf_gets_rerouted(self):
        """The aar.org scenario: server returned HTML at a .pdf URL."""
        from app.services.text_extraction_service import extract_text_with_detection
        html_bytes = b"<!DOCTYPE html><html><body>This is actually HTML content</body></html>"
        text, status, error, actual_ext = extract_text_with_detection(html_bytes, ".pdf")
        assert status == "complete"
        assert actual_ext == ".html"
        assert "actually HTML content" in text

    def test_truly_corrupt_pdf_stays_corrupt_when_no_reroute_available(self):
        from app.services.text_extraction_service import extract_text_with_detection
        # Random binary that doesn't match any recognized magic — no reroute possible
        text, status, error, actual_ext = extract_text_with_detection(b"\x00" * 200, ".pdf")
        assert status == "corrupt"
        assert actual_ext == ".pdf"  # unchanged since detection returned None

    @patch("app.services.text_extraction_service._extract_docx")
    def test_docx_extraction(self, mock_docx):
        mock_docx.return_value = "Word document text"
        text, status, *_ = extract_text(b"fake docx", ".docx")
        assert status == "complete"
        assert text == "Word document text"

    @patch("app.services.text_extraction_service._extract_docx")
    def test_doc_extraction(self, mock_docx):
        """The .doc extension maps to the docx extractor."""
        mock_docx.return_value = "Doc text"
        text, status, *_ = extract_text(b"fake doc", ".doc")
        assert status == "complete"

    @patch("app.services.text_extraction_service._extract_pptx")
    def test_pptx_extraction(self, mock_pptx):
        mock_pptx.return_value = "[Slide 1]\nTitle slide text"
        text, status, *_ = extract_text(b"fake pptx", ".pptx")
        assert status == "complete"
        assert "Slide 1" in text
        mock_pptx.assert_called_once_with(b"fake pptx")

    @patch("app.services.text_extraction_service._extract_pptx")
    def test_ppt_extraction(self, mock_pptx):
        """The .ppt extension maps to the pptx extractor."""
        mock_pptx.return_value = "Presentation text"
        text, status, *_ = extract_text(b"fake ppt", ".ppt")
        assert status == "complete"

    @patch("app.services.text_extraction_service._extract_xlsx")
    def test_xlsx_extraction(self, mock_xlsx):
        mock_xlsx.return_value = "[Sheet: Sheet1]\nA | B\n1 | 2"
        text, status, *_ = extract_text(b"fake xlsx", ".xlsx")
        assert status == "complete"
        assert "Sheet1" in text

    @patch("app.services.text_extraction_service._extract_xls")
    def test_xls_extraction(self, mock_xls):
        """The .xls extension maps to the xls extractor."""
        mock_xls.return_value = "Sheet data"
        text, status, *_ = extract_text(b"fake xls", ".xls")
        assert status == "complete"
        mock_xls.assert_called_once_with(b"fake xls")


class TestSpreadsheetMetadata:
    @patch("openpyxl.load_workbook")
    def test_xlsx_metadata_extraction(self, mock_load):
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("Name", "Department", "Salary"),
            ("Alice", "Engineering", "120000"),
            ("Bob", "Marketing", "95000"),
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Employees"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_ws)
        mock_load.return_value = mock_wb

        result = extract_spreadsheet_metadata(b"fake xlsx", ".xlsx")

        assert "1 sheet(s)" in result
        assert "Employees" in result
        assert "Name | Department | Salary" in result
        assert "Alice" in result
        assert "3 rows" in result

    @patch("openpyxl.load_workbook")
    def test_xlsx_empty_sheet(self, mock_load):
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = []

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Empty"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_ws)
        mock_load.return_value = mock_wb

        result = extract_spreadsheet_metadata(b"fake xlsx", ".xlsx")

        assert "empty sheet" in result

    @patch("openpyxl.load_workbook")
    def test_xlsx_load_failure(self, mock_load):
        mock_load.side_effect = Exception("corrupt file")
        result = extract_spreadsheet_metadata(b"bad data", ".xlsx")
        assert result == ""

    def test_csv_metadata_extraction(self):
        csv_data = "Name,Age,City\nAlice,30,Boston\nBob,25,NYC\n".encode("utf-8")
        result = extract_spreadsheet_metadata(csv_data, ".csv")

        assert "CSV file" in result
        assert "Name | Age | City" in result
        assert "Alice" in result

    def test_csv_empty(self):
        result = extract_spreadsheet_metadata(b"", ".csv")
        assert result == ""

    @patch("xlrd.open_workbook")
    def test_xls_metadata_extraction(self, mock_open):
        mock_sheet = MagicMock()
        mock_sheet.name = "Data"
        mock_sheet.nrows = 3
        mock_sheet.ncols = 2
        mock_sheet.cell_value.side_effect = lambda r, c: [
            ["ID", "Name"],
            ["1", "Alice"],
            ["2", "Bob"],
        ][r][c]

        mock_wb = MagicMock()
        mock_wb.sheet_names.return_value = ["Data"]
        mock_wb.sheets.return_value = [mock_sheet]
        mock_open.return_value = mock_wb

        result = extract_spreadsheet_metadata(b"fake xls", ".xls")

        assert "1 sheet(s)" in result
        assert "Data" in result
        assert "ID | Name" in result
        assert "Alice" in result

    @patch("xlrd.open_workbook")
    def test_xls_metadata_failure(self, mock_open):
        mock_open.side_effect = Exception("corrupt file")
        result = extract_spreadsheet_metadata(b"bad", ".xls")
        assert result == ""

    @patch("openpyxl.load_workbook")
    def test_multiple_sheets(self, mock_load):
        mock_ws1 = MagicMock()
        mock_ws1.iter_rows.return_value = [("Col1", "Col2"), ("a", "b")]

        mock_ws2 = MagicMock()
        mock_ws2.iter_rows.return_value = [("X", "Y"), ("1", "2")]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Data", "Summary"]

        def getitem(name):
            return mock_ws1 if name == "Data" else mock_ws2
        mock_wb.__getitem__ = MagicMock(side_effect=getitem)
        mock_load.return_value = mock_wb

        result = extract_spreadsheet_metadata(b"fake", ".xlsx")

        assert "2 sheet(s)" in result
        assert "Data" in result
        assert "Summary" in result
