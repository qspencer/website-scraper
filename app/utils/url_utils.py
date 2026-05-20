from urllib.parse import urlparse, urljoin, urldefrag, parse_qs, urlencode, urlunparse, unquote
from typing import Optional, Tuple, Set
import re


# Query parameters to strip during crawling (tracking/navigation params)
STRIP_PARAMS = {
    'from_page', 'replytocom', 'utm_source', 'utm_medium', 'utm_campaign',
    'utm_term', 'utm_content', 'fbclid', 'gclid', 'ref', 'source'
}

# URL patterns to skip entirely
SKIP_URL_PATTERNS = [
    r'\?replytocom=',  # Comment reply links
    r'/feed/\?',       # RSS feed variations
    r'/feed$',         # RSS feeds
]


def normalize_url(url: str, base_url: Optional[str] = None) -> str:
    """Normalize a URL by removing fragments and resolving relative paths."""
    if base_url:
        url = urljoin(base_url, url)

    # Remove fragment
    url, _ = urldefrag(url)

    # Ensure proper scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


def normalize_url_for_crawl(url: str) -> str:
    """
    Normalize URL for crawling by stripping tracking/unnecessary query params.
    This helps avoid crawling the same page multiple times with different params.
    """
    parsed = urlparse(url)

    # Parse and filter query parameters
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        # Remove tracking/navigation params
        filtered_params = {
            k: v for k, v in params.items()
            if k.lower() not in STRIP_PARAMS
        }
        # Rebuild query string (sorted for consistency)
        new_query = urlencode(filtered_params, doseq=True) if filtered_params else ''
    else:
        new_query = ''

    # Rebuild URL
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),  # Lowercase domain
        parsed.path.rstrip('/') if parsed.path != '/' else '/',  # Normalize trailing slash
        parsed.params,
        new_query,
        ''  # No fragment
    ))

    return normalized


def should_skip_url(url: str) -> bool:
    """Check if URL should be skipped entirely during crawling."""
    for pattern in SKIP_URL_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def get_domain(url: str) -> str:
    """Extract the domain from a URL."""
    parsed = urlparse(url)
    return parsed.netloc.lower()


def normalize_domain(domain: str) -> str:
    """Normalize a domain by removing www. prefix."""
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs are on the same domain (ignoring www prefix)."""
    domain1 = normalize_domain(get_domain(url1))
    domain2 = normalize_domain(get_domain(url2))
    return domain1 == domain2


def is_internal_link(link: str, base_url: str) -> bool:
    """Check if a link is internal (same domain as base URL)."""
    try:
        normalized = normalize_url(link, base_url)
        return is_same_domain(normalized, base_url)
    except Exception:
        return False


def is_valid_url(url: str) -> bool:
    """Check if a string is a valid URL."""
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme in ("http", "https") and parsed.netloc)
    except Exception:
        return False


def get_filename_from_url(url: str) -> str:
    """Extract filename from URL path."""
    parsed = urlparse(url)
    path = parsed.path

    # Get the last component of the path
    filename = path.rsplit("/", 1)[-1] if "/" in path else path

    # Remove query parameters from filename if present
    if "?" in filename:
        filename = filename.split("?")[0]

    # Decode percent-encoding (stdlib unquote is total — no exception path needed).
    filename = unquote(filename)

    return filename or "unknown"


def get_extension_from_url(url: str) -> str:
    """Extract file extension from URL."""
    filename = get_filename_from_url(url)

    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        # Clean up extension (remove any trailing query params that slipped through)
        ext = re.sub(r"[^a-z0-9.]", "", ext)
        return ext if len(ext) <= 10 else ""

    return ""


def is_likely_html_page(url: str) -> bool:
    """Check if URL is likely an HTML page rather than a file."""
    ext = get_extension_from_url(url)

    # No extension usually means HTML page
    if not ext:
        return True

    # Common HTML extensions
    html_extensions = {".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".shtml"}
    return ext in html_extensions


def clean_url_for_display(url: str, max_length: int = 50) -> str:
    """Truncate URL for display purposes."""
    if len(url) <= max_length:
        return url

    parsed = urlparse(url)
    filename = get_filename_from_url(url)

    # Try to show domain + filename
    short = f"{parsed.netloc}/.../{filename}"
    if len(short) <= max_length:
        return short

    # Just truncate
    return url[: max_length - 3] + "..."
