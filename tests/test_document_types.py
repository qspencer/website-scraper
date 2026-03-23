"""Tests for the document type mapping utility."""

from app.utils.document_types import get_document_type_label, EXTENSION_TYPE_MAP


class TestGetDocumentTypeLabel:
    def test_pdf(self):
        assert get_document_type_label(".pdf") == "PDF Document"

    def test_xlsx(self):
        assert get_document_type_label(".xlsx") == "Excel Spreadsheet"

    def test_xls_legacy(self):
        assert get_document_type_label(".xls") == "Legacy Excel Spreadsheet"

    def test_doc_legacy(self):
        assert get_document_type_label(".doc") == "Legacy Word Document"

    def test_docx(self):
        assert get_document_type_label(".docx") == "Word Document"

    def test_pptx(self):
        assert get_document_type_label(".pptx") == "PowerPoint Presentation"

    def test_ppt_legacy(self):
        assert get_document_type_label(".ppt") == "Legacy PowerPoint Presentation"

    def test_csv(self):
        assert get_document_type_label(".csv") == "CSV Spreadsheet"

    def test_txt(self):
        assert get_document_type_label(".txt") == "Plain Text File"

    def test_case_insensitive(self):
        assert get_document_type_label(".PDF") == "PDF Document"
        assert get_document_type_label(".XLSX") == "Excel Spreadsheet"

    def test_without_leading_dot(self):
        assert get_document_type_label("pdf") == "PDF Document"
        assert get_document_type_label("xlsx") == "Excel Spreadsheet"

    def test_unknown_extension(self):
        assert get_document_type_label(".xyz") == "XYZ File"

    def test_unknown_without_dot(self):
        assert get_document_type_label("abc") == "ABC File"

    def test_whitespace_stripped(self):
        assert get_document_type_label(" .pdf ") == "PDF Document"

    def test_all_mapped_extensions_have_labels(self):
        """Every key in the map should return a non-empty string."""
        for ext in EXTENSION_TYPE_MAP:
            label = get_document_type_label(ext)
            assert isinstance(label, str)
            assert len(label) > 0
