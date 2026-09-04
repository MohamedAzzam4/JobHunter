"""Application-target quality classification.

Every discovered job URL is classified into exactly one quality bucket:

- ``DIRECT_ATS`` — the URL is an applicant-tracking-system posting/apply
  page (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS).
  Eligible for the supervisor pilot.
- ``CAREER_DETAIL_SAFE_APPLY`` — a company career-detail page that can
  carry a safe apply flow. Eligible for the supervisor pilot.
- ``SOURCE_ONLY`` — an aggregator/detail page with no apply flow
  (LinkedIn, Indeed, StepStone, ...). Never supervisor-ready.
- ``UNKNOWN`` — unparseable URL or unrecognized host. Not pilot-eligible
  until a human or a scanner promotes it with evidence.

The classification is URL-only and deterministic (no network). It never
touches candidate data.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DIRECT_ATS = "DIRECT_ATS"
CAREER_DETAIL_SAFE_APPLY = "CAREER_DETAIL_SAFE_APPLY"
SOURCE_ONLY = "SOURCE_ONLY"
UNKNOWN = "UNKNOWN"

PILOT_ELIGIBLE_QUALITIES = frozenset({DIRECT_ATS, CAREER_DETAIL_SAFE_APPLY})

# Host suffixes that identify applicant-tracking-system posting pages.
_ATS_HOST_SUFFIXES: tuple[str, ...] = (
    "boards.greenhouse.io",
    "greenhouse.io",
    "jobs.lever.co",
    "lever.co",
    "myworkdayjobs.com",
    "jobs.smartrecruiters.com",
    "smartrecruiters.com",
    "jobs.ashby.com",
    "ashbyhq.com",
    "icims.com",
)

# Host suffixes that are aggregators/detail pages without an apply flow.
_AGGREGATOR_HOST_SUFFIXES: tuple[str, ...] = (
    "linkedin.com",
    "indeed.com",
    "stepstone.de",
    "stepstone.com",
    "glassdoor.com",
    "glassdoor.de",
    "ziprecruiter.com",
    "monster.com",
    "monster.de",
    "jobworld.de",
    "adzuna.de",
    "xing.com",
)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    """Return True when host equals or is a subdomain of any suffix."""
    host = host.lower()
    for suffix in suffixes:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def classify_target_quality(url: str, source: str = "") -> str:
    """Classify one job URL into a target-quality bucket.

    Args:
        url: The job posting/application URL.
        source: Optional scanner source tag (e.g. "linkedin"). A LinkedIn
            or Indeed source tag forces SOURCE_ONLY even if the URL itself
            looks neutral.

    Returns:
        One of DIRECT_ATS, CAREER_DETAIL_SAFE_APPLY, SOURCE_ONLY, UNKNOWN.
    """
    source_lower = (source or "").lower()
    if "linkedin" in source_lower or "indeed" in source_lower:
        return SOURCE_ONLY

    try:
        parts = urlsplit(url)
    except ValueError:
        return UNKNOWN
    if parts.scheme.lower() not in ("http", "https"):
        return UNKNOWN
    host = (parts.hostname or "").lower()
    if not host:
        return UNKNOWN

    if _host_matches(host, _ATS_HOST_SUFFIXES):
        return DIRECT_ATS
    if _host_matches(host, _AGGREGATOR_HOST_SUFFIXES):
        return SOURCE_ONLY
    return UNKNOWN


def promote_career_detail(url: str, quality: str) -> str:
    """Promote an UNKNOWN same-host career link to CAREER_DETAIL_SAFE_APPLY.

    Used by the websearch scanner, which knows the company career host:
    same-host career links are evidence-backed career-detail pages.
    Anything already classified (or unparseable) is returned unchanged.
    """
    if quality == UNKNOWN and url:
        return CAREER_DETAIL_SAFE_APPLY
    return quality


def is_pilot_eligible(row: dict) -> tuple[bool, str]:
    """Pilot-selection rule for one supervisor-ready export row.

    Returns (eligible, reason). A row is eligible only when ALL hold:
    non-Siemens platform, target quality DIRECT_ATS or
    CAREER_DETAIL_SAFE_APPLY, status ready_to_apply, and both tailored
    PDFs referenced. SOURCE_ONLY/UNKNOWN rows are never supervisor-ready.
    """
    if str(row.get("platform", "")).lower() == "siemens":
        return False, "siemens out of scope"
    quality = str(row.get("target_quality", "") or "")
    if not quality:
        quality = classify_target_quality(
            str(row.get("url", "")), str(row.get("source", ""))
        )
    if quality == SOURCE_ONLY:
        return False, "SOURCE_ONLY has no apply flow"
    if quality not in PILOT_ELIGIBLE_QUALITIES:
        return False, f"target quality {quality} is not pilot eligible"
    if str(row.get("status", "")) != "ready_to_apply":
        return False, f"status {row.get('status')!r} is not ready_to_apply"
    if not row.get("cv_pdf") or not row.get("cover_letter_pdf"):
        return False, "missing tailored document package"
    return True, f"eligible ({quality})"


__all__ = [
    "CAREER_DETAIL_SAFE_APPLY",
    "DIRECT_ATS",
    "PILOT_ELIGIBLE_QUALITIES",
    "SOURCE_ONLY",
    "UNKNOWN",
    "classify_target_quality",
    "is_pilot_eligible",
    "promote_career_detail",
]
