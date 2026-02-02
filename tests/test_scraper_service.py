import pytest
from app.services.scraper_service import ScraperService
from app.core.constants import DocumentTypeFilter


@pytest.fixture
def scraper():
    return ScraperService()


class TestParseLinks:
    def test_extracts_anchor_links(self, scraper, sample_html):
        links = scraper.parse_links(sample_html, "https://example.com")
        assert len(links) > 0

    def test_resolves_relative_links(self, scraper, sample_html):
        links = scraper.parse_links(sample_html, "https://example.com")
        # Check that relative links are resolved
        pdf_links = [l for l in links if "report.pdf" in l]
        assert len(pdf_links) == 1
        assert pdf_links[0].startswith("https://example.com")

    def test_ignores_javascript_links(self, scraper, sample_html):
        links = scraper.parse_links(sample_html, "https://example.com")
        js_links = [l for l in links if "javascript:" in l]
        assert len(js_links) == 0

    def test_ignores_mailto_links(self, scraper, sample_html):
        links = scraper.parse_links(sample_html, "https://example.com")
        mailto_links = [l for l in links if "mailto:" in l]
        assert len(mailto_links) == 0

    def test_ignores_anchor_links(self, scraper, sample_html):
        links = scraper.parse_links(sample_html, "https://example.com")
        # Anchor-only links (#section) should be ignored
        anchor_only = [l for l in links if l == "https://example.com#section"]
        assert len(anchor_only) == 0

    def test_handles_empty_html(self, scraper):
        links = scraper.parse_links("", "https://example.com")
        assert links == []

    def test_handles_malformed_html(self, scraper):
        html = "<a href='test.pdf'>Link</a><a href='other"  # Malformed
        links = scraper.parse_links(html, "https://example.com")
        assert isinstance(links, list)


class TestFilterDocumentLinks:
    def test_common_filter_finds_pdf(self, scraper):
        links = [
            "https://example.com/file.pdf",
            "https://example.com/page.html",
            "https://example.com/doc.docx",
        ]
        result = scraper.filter_document_links(links, DocumentTypeFilter.COMMON)
        assert "https://example.com/file.pdf" in result
        assert "https://example.com/doc.docx" in result
        assert "https://example.com/page.html" not in result

    def test_pdf_only_filter(self, scraper):
        links = [
            "https://example.com/file.pdf",
            "https://example.com/doc.docx",
            "https://example.com/data.xlsx",
        ]
        result = scraper.filter_document_links(links, DocumentTypeFilter.PDF_ONLY)
        assert len(result) == 1
        assert "file.pdf" in result[0]

    def test_all_filter_includes_images(self, scraper):
        links = [
            "https://example.com/file.pdf",
            "https://example.com/image.jpg",
            "https://example.com/photo.png",
        ]
        result = scraper.filter_document_links(links, DocumentTypeFilter.ALL)
        assert len(result) == 3

    def test_case_insensitive(self, scraper):
        links = [
            "https://example.com/file.PDF",
            "https://example.com/doc.DOCX",
        ]
        result = scraper.filter_document_links(links, DocumentTypeFilter.COMMON)
        assert len(result) == 2


class TestFilterPageLinks:
    def test_identifies_html_pages(self, scraper):
        links = [
            "https://example.com/page.html",
            "https://example.com/file.pdf",
            "https://example.com/about",
            "https://example.com/contact.php",
        ]
        result = scraper.filter_page_links(links)
        assert "https://example.com/page.html" in result
        assert "https://example.com/about" in result
        assert "https://example.com/contact.php" in result
        assert "https://example.com/file.pdf" not in result


class TestIsJavascriptRedirect:
    def test_window_location(self, scraper):
        html = "<html><script>window.location = 'http://example.com';</script></html>"
        assert scraper.is_javascript_redirect(html) is True

    def test_document_location(self, scraper):
        html = "<html><script>document.location.href = '/new-page';</script></html>"
        assert scraper.is_javascript_redirect(html) is True

    def test_location_replace(self, scraper):
        html = "<html><script>location.replace('http://example.com');</script></html>"
        assert scraper.is_javascript_redirect(html) is True

    def test_window_onload(self, scraper):
        html = "<html><script>window.onload = function() { redirect(); }</script></html>"
        assert scraper.is_javascript_redirect(html) is True

    def test_normal_short_page(self, scraper):
        # Short page without JS redirect patterns
        html = "<html><body><h1>Hello</h1></body></html>"
        assert scraper.is_javascript_redirect(html) is False

    def test_long_page_with_js(self, scraper):
        # Long page (>500 chars) with JS is not flagged as redirect
        html = "<html><body>" + "x" * 600 + "<script>window.location='/';</script></body></html>"
        assert scraper.is_javascript_redirect(html) is False

    def test_empty_html(self, scraper):
        assert scraper.is_javascript_redirect("") is False

    def test_none_html(self, scraper):
        assert scraper.is_javascript_redirect(None) is False
