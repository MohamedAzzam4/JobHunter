"""Export-time target freshness validation.

Before a supervisor-ready row is published, the application target is
revalidated with a deterministic public lookup where one exists:

- Workday (``*.myworkdayjobs.com``): the requisition ID embedded in the
  application URL (``..._ID12345``) is searched on the tenant/board CXS
  endpoint derived from the URL itself. A matching posting means LIVE;
  an accepted empty result means EXPIRED.
- SmartRecruiters (``jobs.smartrecruiters.com``): the public posting
  detail endpoint answers 200 when LIVE and 404 when EXPIRED.

Outcomes are ``LIVE`` / ``EXPIRED`` / ``UNKNOWN``. Only a positive
EXPIRED excludes the row (skip reason ``APPLICATION_EXPIRED``).
Transport errors, unexpected payloads, and unsupported ATS hosts yield
UNKNOWN, which never blocks export — a transient network failure must
not be misreported as an expired posting.

Freshness metadata: no new timestamp fields are added to rows (the
byte-stability contract forbids them). The existing fields already cover
the handoff needs: ``application_id``, ``url`` (source and target are
identical for direct-ATS rows), ``platform``/``source`` (ATS/provider),
``external_job_id`` (requisition ID where known), ``evaluated_at``,
``date_posted``, and ``metadata.target_quality``.

Pure ``httpx`` + stdlib. Network access happens only inside
:func:`check_target_freshness` / :func:`filter_fresh_rows`, never inside
:func:`utils.queue_exporter.build_queue_entries` (which stays pure).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

LIVE = "LIVE"
EXPIRED = "EXPIRED"
UNKNOWN = "UNKNOWN"

APPLICATION_EXPIRED = "application_expired"

FETCH_TIMEOUT = 15  # seconds

# Matches the trailing requisition token in Workday application URLs, e.g.
# ".../job/Nuremberg/Werkstudent-X--m-w-d-_ID15374" -> "ID15374".
_WORKDAY_ID_RE = re.compile(r"_(ID\d+)\s*$")


def parse_workday_identity(url: str) -> tuple[str, str, str] | None:
    """Split a Workday application URL into (tenant, board, requisition_id).

    Returns None when the URL is not a recognizable Workday application
    URL. Tenant comes from ``{tenant}.wdN.myworkdayjobs.com``; board and
    requisition ID come from ``/{locale}/{board}/job/..._IDxxx``.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    # Host shape is {tenant}.wd{N}.myworkdayjobs.com (e.g. datev.wd3...).
    labels = host[: -len(".myworkdayjobs.com")].split(".")
    if len(labels) != 2 or not labels[0] or not re.fullmatch(r"wd\d+", labels[1]):
        return None
    tenant = labels[0]
    segments = [seg for seg in parts.path.split("/") if seg]
    # Expected shape: {locale}/{board}/job/{slug}_IDxxx
    if len(segments) < 4 or segments[2].lower() != "job":
        return None
    board = segments[1]
    match = _WORKDAY_ID_RE.search(segments[-1])
    if not match:
        return None
    return tenant, board, match.group(1)


def check_workday(url: str, timeout: float = FETCH_TIMEOUT) -> tuple[str, str]:
    """Revalidate one Workday application URL.

    Returns (LIVE, detail) when the tenant index still contains the
    requisition, (EXPIRED, detail) on an accepted empty result, and
    (UNKNOWN, detail) on any transport/protocol surprise.
    """
    identity = parse_workday_identity(url)
    if identity is None:
        return UNKNOWN, "unrecognized Workday application URL shape"
    tenant, board, requisition_id = identity
    endpoint = (
        f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/"
        f"{tenant}/{board}/jobs"
    )
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                endpoint,
                json={
                    "appliedFacets": {},
                    "limit": 5,
                    "offset": 0,
                    "searchText": requisition_id,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                },
            )
    except Exception as e:  # noqa: BLE001 — transient, never expiry
        return UNKNOWN, f"workday lookup transport failure: {type(e).__name__}"
    if resp.status_code != 200:
        return UNKNOWN, f"workday lookup HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return UNKNOWN, "workday lookup returned non-JSON payload"
    if not isinstance(data, dict):
        return UNKNOWN, "workday lookup returned unexpected payload shape"
    postings = data.get("jobPostings", [])
    if not isinstance(postings, list):
        return UNKNOWN, "workday lookup returned unexpected postings shape"
    token = f"_{requisition_id}"
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        if token in str(posting.get("externalPath", "")):
            return LIVE, f"requisition {requisition_id} present in tenant index"
    return EXPIRED, f"requisition {requisition_id} absent from tenant index"


def check_smartrecruiters(url: str, timeout: float = FETCH_TIMEOUT) -> tuple[str, str]:
    """Revalidate one SmartRecruiters apply URL via the public detail API.

    HTTP 200 means LIVE, HTTP 404 means EXPIRED, anything else is UNKNOWN.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return UNKNOWN, "unparseable URL"
    host = (parts.hostname or "").lower()
    if host != "jobs.smartrecruiters.com":
        return UNKNOWN, "not a SmartRecruiters apply URL"
    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) < 2:
        return UNKNOWN, "SmartRecruiters URL missing company/posting segments"
    company, posting_slug = segments[0], segments[1]
    posting_id = posting_slug.split("-")[0]
    if not posting_id:
        return UNKNOWN, "SmartRecruiters URL missing posting id"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                "https://api.smartrecruiters.com/v1/companies/"
                f"{company}/postings/{posting_id}",
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            )
    except Exception as e:  # noqa: BLE001 — transient, never expiry
        return UNKNOWN, f"smartrecruiters lookup transport failure: {type(e).__name__}"
    if resp.status_code == 200:
        return LIVE, f"posting {posting_id} present"
    if resp.status_code == 404:
        return EXPIRED, f"posting {posting_id} returns 404"
    return UNKNOWN, f"smartrecruiters lookup HTTP {resp.status_code}"


def check_target_freshness(
    url: str, platform: str = "", source: str = ""
) -> tuple[str, str]:
    """Revalidate one application target URL.

    Dispatches on host (platform/source are hints only). Returns
    (LIVE|EXPIRED|UNKNOWN, sanitized detail). Hosts without a
    deterministic lookup yield UNKNOWN.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return UNKNOWN, "unparseable URL"
    _ = (platform, source)  # hints reserved for future dispatch
    if host.endswith(".myworkdayjobs.com"):
        return check_workday(url)
    if host == "jobs.smartrecruiters.com":
        return check_smartrecruiters(url)
    return UNKNOWN, "no deterministic lookup for this host"


def filter_fresh_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split export rows into (fresh_rows, freshness_skipped).

    Only positively EXPIRED rows are excluded (skip-record reason
    ``APPLICATION_EXPIRED``). UNKNOWN rows are kept — transient failures
    must not masquerade as expiry.
    """
    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unknown = 0
    for row in rows:
        url = str(row.get("url", ""))
        status, detail = check_target_freshness(
            url, str(row.get("platform", "")), str(row.get("source", ""))
        )
        if status == EXPIRED:
            skipped.append(
                {
                    "url": url,
                    "company": str(row.get("company", "")),
                    "title": str(row.get("title", "")),
                    "reason": APPLICATION_EXPIRED,
                    "detail": detail,
                }
            )
        else:
            if status == UNKNOWN:
                unknown += 1
            fresh.append(row)
    if unknown:
        logger.info(
            "freshness: %d row(s) kept with UNKNOWN target status "
            "(no deterministic lookup or transient failure)",
            unknown,
        )
    return fresh, skipped


__all__ = [
    "APPLICATION_EXPIRED",
    "EXPIRED",
    "FETCH_TIMEOUT",
    "LIVE",
    "UNKNOWN",
    "check_smartrecruiters",
    "check_target_freshness",
    "check_workday",
    "filter_fresh_rows",
    "parse_workday_identity",
]
