"""
Job description fetcher.

Fetches full JD text from a URL. Handles:
- Static HTML pages (httpx)
- SPAs / JS-rendered pages (Playwright fallback)
- Expired/closed job detection
- PDF/image detection (unsupported)
- Timeout and error handling
"""

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Signals that a job posting is expired/closed
EXPIRED_SIGNALS = [
    "job no longer available",
    "no longer open",
    "position has been filled",
    "this job has expired",
    "page not found",
    "no longer accepting applications",
    "diese stelle ist nicht mehr verfügbar",
    "stellenangebot nicht mehr verfügbar",
    "?error=true",
]

FETCH_TIMEOUT = 15  # seconds
MAX_JD_LENGTH = 15000  # chars — truncate very long JDs to fit model context


@dataclass
class FetchResult:
    """Result of fetching a job description."""
    url: str
    success: bool
    title: str = ""
    text: str = ""
    is_expired: bool = False
    error: str = ""
    method: str = "httpx"  # "httpx" or "playwright"
    truncated: bool = False


def _clean_html_to_text(html: str) -> str:
    """Basic HTML to text conversion (no external deps needed)."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_linkedin_jd(html: str) -> str:
    """Extract job description from LinkedIn's specific HTML structure.
    
    LinkedIn renders JD content inside a 'show-more-less-html__markup' div.
    Extracting from this div directly avoids polluting the JD text with
    navigation bars, footers, and sidebar content from the full page.
    
    Returns:
        Clean JD text, or empty string if the pattern isn't found.
    """
    match = re.search(
        r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return _clean_html_to_text(match.group(1))


def _check_expired(text: str, url: str) -> bool:
    """Check if the page content indicates an expired job posting."""
    lower = text.lower()
    for signal in EXPIRED_SIGNALS:
        if signal in lower:
            return True
    if "?error=true" in url:
        return True
    # Very short content (< 300 chars of actual text) = probably just nav/footer
    if len(text.strip()) < 300:
        return True
    return False


def _extract_jsonld_jd(html: str) -> tuple[str, str]:
    """Extract a JobPosting description from JSON-LD structured data.

    Many ATS/career pages (e.g. Workday) render job content client-side but
    embed a standards-compliant ``application/ld+json`` JobPosting block for
    SEO. Returns (title, text); empty strings when no usable block exists.
    Pure function — hermetically testable.
    """
    import json as _json

    blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    candidates: list[dict] = []
    for block in blocks:
        try:
            data = _json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            # Unwrap @graph containers.
            if "@graph" in item and isinstance(item["@graph"], list):
                candidates.extend(
                    node for node in item["@graph"] if isinstance(node, dict)
                )
            else:
                candidates.append(item)

    for node in candidates:
        node_type = node.get("@type", "")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not any(str(t).lower() == "jobposting" for t in types):
            continue
        description = node.get("description", "")
        if not isinstance(description, str) or len(description.strip()) < 300:
            continue
        title = node.get("title", "")
        if not isinstance(title, str):
            title = ""
        return title.strip(), _clean_html_to_text(description)

    return "", ""


def _smartrecruiters_api_detail(url: str) -> tuple[str | None, str | None]:
    """Split a jobs.smartrecruiters.com apply URL into (company, posting_id).

    Returns (None, None) when the URL is not a SmartRecruiters apply URL.
    The posting id may carry an SEO slug suffix (``{id}-{slug}``); only the
    leading numeric id is returned.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return None, None
    host = (parts.hostname or "").lower()
    if host != "jobs.smartrecruiters.com":
        return None, None
    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) < 2:
        return None, None
    posting_id = segments[1].split("-")[0]
    if not posting_id:
        return None, None
    return segments[0], posting_id


def _extract_title_from_html(html: str) -> str:
    """Try to extract job title from HTML."""
    # Try <title> tag
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = _clean_html_to_text(match.group(1))
        # Clean common suffixes
        for sep in [" | ", " - ", " — ", " – ", " :: "]:
            if sep in title:
                title = title.split(sep)[0].strip()
        return title
    return ""


async def _fetch_smartrecruiters_detail(url: str) -> FetchResult | None:
    """Fetch a JD via the SmartRecruiters public postings API.

    Applies only to ``jobs.smartrecruiters.com`` apply URLs. Returns a
    populated FetchResult on success, or None to fall through to the
    generic HTML path (never raises).
    """
    result = FetchResult(url=url, success=False)
    company, posting_id = _smartrecruiters_api_detail(url)
    if not company or not posting_id:
        return None
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        ) as client:
            resp = await client.get(
                f"https://api.smartrecruiters.com/v1/companies/"
                f"{company}/postings/{posting_id}"
            )
    except Exception as e:  # noqa: BLE001 — fall through to HTML path
        logger.info(f"SmartRecruiters API unavailable, falling back: {e}")
        return None

    if resp.status_code == 404:
        result.is_expired = True
        result.error = "404 Not Found"
        return result
    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None
    sections = (data.get("jobAd", {}) or {}).get("sections", {}) or {}
    parts = []
    for key in (
        "jobDescription",
        "qualifications",
        "companyDescription",
        "additionalInformation",
    ):
        section = sections.get(key, {}) or {}
        text = section.get("text", "") or ""
        if not isinstance(text, str) or not text.strip():
            continue
        title = section.get("title", "") or key
        parts.append(f"## {title}\n{text.replace('&#xa0;', ' ')}")
    if not parts:
        return None

    text = _clean_html_to_text("\n\n".join(parts))
    if _check_expired(text, url):
        result.is_expired = True
        result.error = "Job posting appears expired"
        return result
    if len(text) > MAX_JD_LENGTH:
        text = text[:MAX_JD_LENGTH]
        result.truncated = True
    result.success = True
    result.title = str(data.get("name", "") or "")
    result.text = text
    result.method = "smartrecruiters-api"
    return result


async def fetch_jd(url: str) -> FetchResult:
    """Fetch a job description from a URL.

    Tries, in order: the SmartRecruiters public API (for its apply URLs),
    plain httpx HTML, embedded JSON-LD JobPosting data (for JS-rendered
    ATS pages), then falls back to Playwright if content looks like an SPA.
    
    Args:
        url: The job posting URL
        
    Returns:
        FetchResult with success status, text, and metadata
    """
    result = FetchResult(url=url, success=False)

    # 0. SmartRecruiters apply URLs: use the public postings API directly
    # (full description without a browser).
    try:
        sr_result = await _fetch_smartrecruiters_detail(url)
        if sr_result is not None:
            return sr_result
    except Exception as e:  # noqa: BLE001 — fall through to HTML path
        logger.info(f"SmartRecruiters fast path failed, falling back: {e}")

    # 1. Try httpx (static HTML)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        ) as client:
            resp = await client.get(url)

            # Check for redirect to error page
            if "?error=true" in str(resp.url):
                result.is_expired = True
                result.error = "Redirected to error page (job closed)"
                return result

            if resp.status_code == 404:
                result.is_expired = True
                result.error = "404 Not Found"
                return result

            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}"
                return result

            content_type = resp.headers.get("content-type", "")

            # Check for PDF/image (unsupported)
            if "application/pdf" in content_type:
                result.error = "JD is a PDF (unsupported)"
                return result
            if content_type.startswith("image/"):
                result.error = "JD is an image (unsupported)"
                return result

            html = resp.text
            title = _extract_title_from_html(html)

            # For LinkedIn, extract JD from the specific markup div
            # (avoids polluting text with nav/footer/sidebar noise)
            if "linkedin.com" in url:
                linkedin_text = _extract_linkedin_jd(html)
                if linkedin_text and len(linkedin_text) > 100:
                    text = linkedin_text
                    logger.info(
                        "LinkedIn JD extracted (%d chars)", len(text)
                    )
                else:
                    # LinkedIn markup not found — fall back to full page
                    text = _clean_html_to_text(html)
            else:
                text = _clean_html_to_text(html)

            # JSON-LD JobPosting fallback: JS-rendered ATS/career pages
            # (e.g. Workday) ship an SEO structured-data block with the
            # full description even when the visible HTML is a thin shell.
            if len(text.strip()) < 300:
                ld_title, ld_text = _extract_jsonld_jd(html)
                if len(ld_text.strip()) >= 300:
                    logger.info("JSON-LD JD extracted (%d chars)", len(ld_text))
                    if _check_expired(ld_text, str(resp.url)):
                        result.is_expired = True
                        result.title = ld_title or title
                        result.error = "Job posting appears expired"
                        return result
                    text = ld_text
                    if ld_title:
                        title = ld_title

            # Check if expired
            if _check_expired(text, str(resp.url)):
                result.is_expired = True
                result.title = title
                result.error = "Job posting appears expired"
                return result

            # Truncate if too long
            if len(text) > MAX_JD_LENGTH:
                text = text[:MAX_JD_LENGTH]
                result.truncated = True
                logger.warning(f"JD truncated to {MAX_JD_LENGTH} chars: {url}")

            result.success = True
            result.title = title
            result.text = text
            result.method = "httpx"
            return result

    except httpx.TimeoutException:
        result.error = f"Timeout after {FETCH_TIMEOUT}s"
        logger.warning(f"Timeout fetching JD: {url}")
        return result
    except httpx.ConnectError as e:
        result.error = f"Connection error: {e}"
        logger.warning(f"Connection error fetching JD: {url}")
        return result
    except Exception as e:
        result.error = f"Unexpected error: {e}"
        logger.error(f"Error fetching JD from {url}: {e}")
        return result


async def fetch_jd_playwright(url: str) -> FetchResult:
    """Fetch JD using Playwright (for JS-rendered/SPA pages).
    
    Only use this as a fallback when httpx returns too little content.
    Playwright is heavier and slower but handles SPAs.
    """
    result = FetchResult(url=url, success=False, method="playwright")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        result.error = "Playwright not installed. Run: pip install playwright && playwright install chromium"
        return result

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto(url, wait_until="networkidle", timeout=20000)
            html = await page.content()
            title = await page.title()

            await browser.close()

            text = _clean_html_to_text(html)

            if _check_expired(text, url):
                result.is_expired = True
                result.title = title
                result.error = "Job posting appears expired"
                return result

            if len(text) > MAX_JD_LENGTH:
                text = text[:MAX_JD_LENGTH]
                result.truncated = True

            result.success = True
            result.title = title
            result.text = text
            return result

    except Exception as e:
        result.error = f"Playwright error: {e}"
        logger.error(f"Playwright error fetching {url}: {e}")
        return result
