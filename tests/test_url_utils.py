import pytest
from app.utils.url_utils import (
    normalize_url,
    normalize_url_for_crawl,
    should_skip_url,
    get_domain,
    normalize_domain,
    is_same_domain,
    is_internal_link,
    is_valid_url,
    get_filename_from_url,
    get_extension_from_url,
    is_likely_html_page,
    clean_url_for_display,
)


class TestNormalizeUrl:
    def test_relative_url(self):
        result = normalize_url("/path/to/file.pdf", "https://example.com")
        assert result == "https://example.com/path/to/file.pdf"

    def test_absolute_url(self):
        result = normalize_url("https://other.com/file.pdf", "https://example.com")
        assert result == "https://other.com/file.pdf"

    def test_removes_fragment(self):
        result = normalize_url("https://example.com/page#section")
        assert result == "https://example.com/page"

    def test_adds_https(self):
        result = normalize_url("example.com/page")
        assert result == "https://example.com/page"


class TestGetDomain:
    def test_simple_domain(self):
        assert get_domain("https://example.com/path") == "example.com"

    def test_subdomain(self):
        assert get_domain("https://www.example.com/path") == "www.example.com"

    def test_with_port(self):
        assert get_domain("https://example.com:8080/path") == "example.com:8080"


class TestNormalizeDomain:
    def test_removes_www(self):
        assert normalize_domain("www.example.com") == "example.com"

    def test_no_www(self):
        assert normalize_domain("example.com") == "example.com"

    def test_lowercase(self):
        assert normalize_domain("WWW.EXAMPLE.COM") == "example.com"

    def test_subdomain_not_www(self):
        assert normalize_domain("api.example.com") == "api.example.com"

    def test_www_in_middle(self):
        # www. should only be stripped from the beginning
        assert normalize_domain("notwww.example.com") == "notwww.example.com"


class TestIsSameDomain:
    def test_same_domain(self):
        assert is_same_domain("https://example.com/a", "https://example.com/b") is True

    def test_different_domain(self):
        assert is_same_domain("https://example.com/a", "https://other.com/b") is False

    def test_case_insensitive(self):
        assert is_same_domain("https://Example.COM/a", "https://example.com/b") is True

    def test_www_vs_no_www(self):
        assert is_same_domain("https://www.example.com/a", "https://example.com/b") is True

    def test_no_www_vs_www(self):
        assert is_same_domain("https://example.com/a", "https://www.example.com/b") is True

    def test_both_www(self):
        assert is_same_domain("https://www.example.com/a", "https://www.example.com/b") is True


class TestIsInternalLink:
    def test_internal_link(self):
        assert is_internal_link("/page.html", "https://example.com") is True

    def test_same_domain_absolute(self):
        assert is_internal_link("https://example.com/page", "https://example.com") is True

    def test_external_link(self):
        assert is_internal_link("https://other.com/page", "https://example.com") is False

    def test_www_link_from_non_www_base(self):
        assert is_internal_link("https://www.example.com/page", "https://example.com") is True

    def test_non_www_link_from_www_base(self):
        assert is_internal_link("https://example.com/page", "https://www.example.com") is True


class TestIsValidUrl:
    def test_valid_https(self):
        assert is_valid_url("https://example.com") is True

    def test_valid_http(self):
        assert is_valid_url("http://example.com") is True

    def test_invalid_no_scheme(self):
        assert is_valid_url("example.com") is False

    def test_invalid_empty(self):
        assert is_valid_url("") is False


class TestGetFilenameFromUrl:
    def test_simple_filename(self):
        assert get_filename_from_url("https://example.com/file.pdf") == "file.pdf"

    def test_nested_path(self):
        assert get_filename_from_url("https://example.com/a/b/c/doc.docx") == "doc.docx"

    def test_with_query_string(self):
        assert get_filename_from_url("https://example.com/file.pdf?v=1") == "file.pdf"

    def test_no_filename(self):
        result = get_filename_from_url("https://example.com/")
        assert result == "" or result == "unknown"


class TestGetExtensionFromUrl:
    def test_pdf(self):
        assert get_extension_from_url("https://example.com/file.pdf") == ".pdf"

    def test_docx(self):
        assert get_extension_from_url("https://example.com/file.docx") == ".docx"

    def test_uppercase(self):
        assert get_extension_from_url("https://example.com/file.PDF") == ".pdf"

    def test_no_extension(self):
        assert get_extension_from_url("https://example.com/page") == ""

    def test_with_query(self):
        assert get_extension_from_url("https://example.com/file.xlsx?download=1") == ".xlsx"


class TestIsLikelyHtmlPage:
    def test_html_extension(self):
        assert is_likely_html_page("https://example.com/page.html") is True

    def test_php_extension(self):
        assert is_likely_html_page("https://example.com/page.php") is True

    def test_no_extension(self):
        assert is_likely_html_page("https://example.com/page") is True

    def test_pdf_not_html(self):
        assert is_likely_html_page("https://example.com/file.pdf") is False

    def test_docx_not_html(self):
        assert is_likely_html_page("https://example.com/file.docx") is False


class TestCleanUrlForDisplay:
    def test_short_url(self):
        url = "https://example.com/file.pdf"
        assert clean_url_for_display(url, 50) == url

    def test_long_url_truncated(self):
        url = "https://example.com/very/long/path/to/some/file.pdf"
        result = clean_url_for_display(url, 30)
        assert len(result) <= 30
        # Either ends with ... or uses domain/.../filename format
        assert "..." in result


class TestNormalizeUrlForCrawl:
    def test_strips_from_page_param(self):
        url = "https://example.com/page?from_page=browse"
        result = normalize_url_for_crawl(url)
        assert "from_page" not in result

    def test_strips_replytocom_param(self):
        url = "https://example.com/post?replytocom=12345"
        result = normalize_url_for_crawl(url)
        assert "replytocom" not in result

    def test_strips_utm_params(self):
        url = "https://example.com/page?utm_source=google&utm_medium=cpc"
        result = normalize_url_for_crawl(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result

    def test_preserves_meaningful_params(self):
        url = "https://example.com/search?q=test&page=2"
        result = normalize_url_for_crawl(url)
        assert "q=test" in result
        assert "page=2" in result

    def test_normalizes_domain_case(self):
        url = "https://EXAMPLE.COM/Page"
        result = normalize_url_for_crawl(url)
        assert "example.com" in result

    def test_removes_trailing_slash(self):
        url = "https://example.com/page/"
        result = normalize_url_for_crawl(url)
        assert result == "https://example.com/page"

    def test_keeps_root_slash(self):
        url = "https://example.com/"
        result = normalize_url_for_crawl(url)
        assert result == "https://example.com/"

    def test_no_query_params(self):
        url = "https://example.com/page"
        result = normalize_url_for_crawl(url)
        assert result == "https://example.com/page"

    def test_mixed_params_strips_tracking_only(self):
        url = "https://example.com/page?id=123&from_page=nav&filter=active"
        result = normalize_url_for_crawl(url)
        assert "id=" in result
        assert "filter=" in result
        assert "from_page" not in result


class TestShouldSkipUrl:
    def test_skips_replytocom(self):
        url = "https://example.com/post?replytocom=12345"
        assert should_skip_url(url) is True

    def test_skips_feed_with_query(self):
        url = "https://example.com/feed/?post_type=post"
        assert should_skip_url(url) is True

    def test_skips_feed_endpoint(self):
        url = "https://example.com/blog/feed"
        assert should_skip_url(url) is True

    def test_allows_normal_url(self):
        url = "https://example.com/page"
        assert should_skip_url(url) is False

    def test_allows_url_with_params(self):
        url = "https://example.com/page?id=123"
        assert should_skip_url(url) is False

    def test_allows_similar_but_not_feed(self):
        url = "https://example.com/feedback"
        assert should_skip_url(url) is False
