"""Websearch/career-page scanner — no-key career discovery.

Covers companies configured with ``scan_method: websearch``. Public web
search scraping is bot-walled from most environments, so this scanner does
career-page discovery instead: it fetches the company's ``careers_url``
(plus any ``discovery_urls``) with plain httpx and extracts same-host job
links and known-ATS links.

Each kept link is classified with
:func:`utils.target_quality.classify_target_quality`; same-host career
links are evidence-backed career-detail pages and are promoted to
``CAREER_DETAIL_SAFE_APPLY``. Aggregator links are dropped — they would be
``SOURCE_ONLY`` and can never be supervisor-ready.

Link text becomes the job title (the site's own anchor text — never
invented). Location is unknown at discovery time ("") and the normal
downstream filters/evaluation handle the rest.

No new dependencies (httpx + stdlib only). Failures are recorded with a
failure category, never reported as zero jobs.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .base import (
    BaseScanner,
    JobPosting,
    ScanResult,
    classify_exception,
    classify_http_status,
)
from utils.target_quality import (
    CAREER_DETAIL_SAFE_APPLY,
    DIRECT_ATS,
    SOURCE_ONLY,
    UNKNOWN,
    classify_target_quality,
    promote_career_detail,
)

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 20
MAX_LINKS_PER_COMPANY = 25

# Path tokens that mark a same-host link as a probable job/career page.
_JOB_PATH_TOKENS: tuple[str, ...] = (
    "job",
    "jobs",
    "stelle",
    "stellen",
    "stellenangebot",
    "karriere",
    "career",
    "careers",
    "vacancy",
    "vacancies",
    "offene-stellen",
    "job-openings",
    "stellenangebote",
    "jobsearch",
    "stelle-",
    "job-",
)

# Link targets that are never jobs.
_SKIP_SUFFIXES: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css", ".js",
    ".pdf", ".mp4", ".xml", ".json",
)


@dataclass
class CareerSite:
    """One configured websearch company."""

    company_name: str
    careers_url: str
    source_tag: str


class WebSearchScanner(BaseScanner):
    """Career-page discovery for ``scan_method: websearch`` companies."""

    def __init__(self, config: dict):
        super().__init__(config, name="websearch")
        self.sites = self._load_sites(config)

    def _load_sites(self, config: dict) -> list[CareerSite]:
        """Load enabled websearch companies from config."""
        sites = []
        for company in config.get("tracked_companies", []):
            if not company.get("enabled", True):
                continue
            if company.get("scan_method") != "websearch":
                continue
            careers_url = (company.get("careers_url") or "").strip()
            if not careers_url:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", company["name"].lower()).strip("-")
            sites.append(CareerSite(
                company_name=company["name"],
                careers_url=careers_url,
                source_tag=f"websearch-{slug}",
            ))
        self.logger.info(f"Loaded {len(sites)} websearch companies")
        return sites

    async def scan(self) -> ScanResult:
        """Fetch each career page and extract job links."""
        result = self._make_result()

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            for site in self.sites:
                try:
                    jobs = await self._scan_site(client, site)
                    result.jobs.extend(jobs)
                except Exception as e:
                    result.note_error(
                        f"{site.company_name}: {e}", classify_exception(e)
                    )
                    self.logger.warning(f"{site.company_name}: {e}")
                await asyncio.sleep(1)  # Be polite between companies

        return self._finish_result(result)

    async def _scan_site(
        self, client: httpx.AsyncClient, site: CareerSite
    ) -> list[JobPosting]:
        """Fetch one career page and extract candidate job links."""
        try:
            resp = await client.get(
                site.careers_url,
                headers={
                    "Accept": "text/html",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"
                    ),
                },
            )
        except Exception as e:
            raise RuntimeError(f"career page fetch failed: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"career page HTTP {resp.status_code} "
                f"[{classify_http_status(resp.status_code)}]"
            )

        return extract_career_links(resp.text, site)


def _clean_anchor(raw: str) -> str:
    """Strip tags/entities from anchor HTML, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def _is_jobish(path: str) -> bool:
    """Heuristic: does a URL path look like a job/career page?"""
    lowered = path.lower()
    return any(token in lowered for token in _JOB_PATH_TOKENS)


def extract_career_links(html: str, site: CareerSite) -> list[JobPosting]:
    """Pure helper: extract candidate JobPostings from career-page HTML.

    Kept separate for hermetic testing (no network).
    """
    try:
        base_host = (urlsplit(site.careers_url).hostname or "").lower()
    except ValueError:
        return []
    base_root = base_host[4:] if base_host.startswith("www.") else base_host

    postings: list[JobPosting] = []
    seen: set[str] = set()

    for match in re.finditer(
        r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        href, anchor_raw = match.group(1).strip(), match.group(2)
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(site.careers_url, href).split("?")[0]
            parts = urlsplit(absolute)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https"):
            continue
        host = (parts.hostname or "").lower()
        if not host or absolute in seen:
            continue
        if absolute.lower().endswith(_SKIP_SUFFIXES):
            continue

        quality = classify_target_quality(absolute, site.source_tag)
        if quality == SOURCE_ONLY:
            continue
        host_root = host[4:] if host.startswith("www.") else host
        if quality == UNKNOWN:
            # Keep same-host job-ish links as evidence-backed career pages;
            # drop everything else (unknown off-site links).
            if host_root != base_root or not _is_jobish(parts.path):
                continue
            quality = promote_career_detail(absolute, quality)
        if quality not in (DIRECT_ATS, CAREER_DETAIL_SAFE_APPLY):
            continue

        title = _clean_anchor(anchor_raw)
        if not title:
            # Fall back to a readable slug — never empty (empty titles
            # fail downstream filters).
            slug = parts.path.rstrip("/").rsplit("/", 1)[-1]
            title = re.sub(r"[-_]+", " ", slug).strip() or absolute

        seen.add(absolute)
        postings.append(JobPosting(
            title=title[:200],
            url=absolute,
            company=site.company_name,
            location="",
            source=site.source_tag,
            target_quality=quality,
        ))
        if len(postings) >= MAX_LINKS_PER_COMPANY:
            break

    return postings


__all__ = ["CareerSite", "WebSearchScanner", "extract_career_links"]
