"""Queue exporter — writes application_queue.jsonl for UniversalAutoApplier.

Per ROADMAP.md WP 1.2 and DATA_CONTRACTS.md (WQ-2):

- Export only jobs that are eligible for application under the existing
  JobHunter/UAA contract:
  - passed evaluation (success=True)
  - are above threshold
  - are recommended for apply
  - have tailored CV and cover letter artifacts (PDFs) that exist on disk
- Do not export rejected, stale, duplicate, or already-applied jobs.
- URL scheme must be HTTP(S) with a hostname (otherwise UAA rejects the row,
  so such a job is skipped with ``invalid_url``).
- Jobs excluded from export are counted and reported as structured skip
  reasons (``skipped_reasons`` / ``skipped_jobs`` in the summary) so that
  run_all can report the handoff accurately. We never fabricate values: a
  missing value or missing file skips the job with an explicit reason.
- Output format: one JSON object per line (JSONL).

Identity contract (must match UAA core/identity.py in full — see
``tests/test_identity_golden_contract.py`` for fixed golden SHAs):

- ``application_id`` is ``sha256(identity_source).hexdigest()``.
- ``identity_source`` is ``platform + ":" + external_job_id''`` when
  BOTH are set and ``external_job_id`` is non-empty after stripping (a
  whitespace-only external id falls back to the canonical URL). Otherwise
  ``identity_source`` is the canonical URL.
- Canonical URL: lowercase scheme and host only; preserve userinfo exactly;
  drop the fragment and default ports (80/443); drop a trailing slash except
  at the host root; strip query keys that start with ``utm_`` or are one of
  ``gclid, fbclid, mc_cid, mc_eid, ref, refid, trackingid`` (case-insensitive
  match, but KEPT keys keep their original case, e.g. ``jobId``); sort
  remaining query pairs by key then value and re-encode.

External-ID normalization policy (JobHunter/UAA boundary):

- The raw ``external_job_id`` (or ``job_id`` as the fallback source ONLY when
  ``external_job_id`` is entirely absent) accepts non-boolean strings and
  integers. Valid values are converted to a stripped non-empty string;
  ``"0"`` and numeric ``0`` are valid IDs (``0`` is NOT lost to truthiness).
  An integer ``512492`` produces the same identity as the string ``"512492"``
  (UAA's lax ``str`` coercion of the field agrees).
- Whitespace-only strings, ``None``, ``bool``, ``dict``, ``list``, ``float``,
  and any other unsupported value become ABSENT: identity falls back to the
  canonical URL, and the emitted ``external_job_id`` field is ``null`` (never
  an int/bool/collection/whitespace-only string UAA might reject). Malformed
  identifiers are therefore never a crash — they degrade to URL identity.

Candidate snapshot truthfulness:

- ``metadata.candidate_profile`` contains ONLY values explicitly present in
  ``config/profile.yml``. Absent/null/blank/whitespace-only values are
  omitted; ``requires_sponsorship`` is emitted only when the source profile
  explicitly stores a Python bool; no fields are invented (no website, no
  work authorization, no years of experience, no forced ``False``).

Determinism and idempotency:

- Processing order is independent of the ordering of ``evaluations.json``:
  evaluations are sorted by URL before filtering, and the final rows are
  sorted by ``application_id``. Identical inputs therefore produce identical
  (byte-stable, no timestamps) queue content.
- No duplicate URLs AND no duplicate ``application_id`` values may appear in
  one export; the first deterministically-seen row wins and later rows are
  skipped with ``duplicate_url`` / ``duplicate_application_id`` reasons.
- Re-running the export replaces the queue (it never appends), so the export
  is idempotent.

Atomic publish:

- The JSONL is written to a uniquely-named temporary file inside the
  destination directory, flushed/closed, and then atomically replaced over
  the final path (``os.replace``). A reader never sees a partially-written
  queue file, and a failed write leaves the previous completed queue intact
  (the temp file is removed on error).

Missing-stage note (reported for WQ-2):

- This pipeline has NO standalone document-generation/tailoring stage. The
  tailored CV and cover letter are generated inside ``run_evaluate`` when the
  global score passes the threshold. The exporter only includes jobs whose
  PDFs actually exist on disk; jobs whose PDF generation failed are skipped
  with ``missing_documents``/``document_not_found``.
- ``tailored_at`` is never persisted by the current pipeline, so it is not
  emitted (it stays absent, matching the "never invent facts" rule).

The exporter reads from:
- ``data/evaluations.json`` (the evaluator's output records)
- ``data/pipeline.md`` (the scanner's job list with URLs/companies/titles)
- ``config/profile.yml`` (the candidate profile snapshot + queue config)

And writes:
- the queue path configured under ``profile.yml -> queue_export ->
  output_path``, defaulting to ``data/application_queue.jsonl``.

Each queue row contains the full ApplicationJob contract fields plus a
candidate profile snapshot under ``metadata.candidate_profile`` so that
UniversalAutoApplier can fill forms without an empty CandidateProfile().

The exporter is intentionally pure (no network, no LLM calls) so it can
run after the evaluate phase as a separate step or from ``run_all``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("queue_exporter")

# Structure skip reasons used when a job is excluded from the export. Kept
# as exact strings so run_all and the unit tests match on them.
EVALUATION_FAILED = "evaluation_failed"
MISSING_URL = "missing_url"
INVALID_URL = "invalid_url"
INVALID_SCORE = "invalid_score"
BELOW_THRESHOLD = "below_threshold"
NOT_RECOMMENDED = "not_recommended"
DUPLICATE_URL = "duplicate_url"
MISSING_DOCUMENTS = "missing_documents"
DOCUMENT_NOT_FOUND = "document_not_found"
MISSING_REQUIRED_FIELDS = "missing_required_fields"
DUPLICATE_APPLICATION_ID = "duplicate_application_id"
APPLICATION_EXPIRED = "application_expired"

SKIP_REASONS: frozenset[str] = frozenset(
    {
        EVALUATION_FAILED,
        MISSING_URL,
        INVALID_URL,
        INVALID_SCORE,
        BELOW_THRESHOLD,
        NOT_RECOMMENDED,
        DUPLICATE_URL,
        MISSING_DOCUMENTS,
        DOCUMENT_NOT_FOUND,
        MISSING_REQUIRED_FIELDS,
        DUPLICATE_APPLICATION_ID,
        APPLICATION_EXPIRED,
    }
)


def load_profile(profile_path: Path = Path("config/profile.yml")) -> dict[str, Any]:
    """Load the candidate profile YAML with gitignored local PII override.

    The override applies only to the default tracked profile; explicit
    custom paths (hermetic tests, ``--profile``) load exactly the given
    file. Returns an empty dict if the file does not exist.
    """
    from utils.profile_loader import load_profile_with_local

    return load_profile_with_local(profile_path)


def extract_candidate_profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    """Extract a truthful candidate profile snapshot for UAA.

    UAA's CandidateProfile model uses optional fields (all ``None`` by
    default), so a sparse snapshot is contract-safe. This function emits
    ONLY values explicitly present in ``config/profile.yml``:

    - Absent, null, blank, or whitespace-only personal values are omitted.
    - ``requires_sponsorship`` is emitted ONLY when the source candidate
      dict explicitly contains a Python ``bool`` — never coerced from a
      string, never defaulted to ``False``.
    - Nothing is invented: no ``website``, ``work_authorization``, or
      ``years_of_experience`` unless the source actually provides them
      (JobHunter's profile.yml never does, so those keys stay absent).
    - Real values (name, email, phone, LinkedIn, GitHub, city/country,
      current position) are kept when actually present. ``first_name`` /
      ``last_name`` and ``city`` / ``country`` are derived by splitting the
      present ``full_name`` / ``location`` — faithful to the data, never a
      default.

    Returns:
        A dict containing only truthful values for a partial snapshot. An
        empty dict is valid and leaves UAA's fallback profile untouched.
    """
    cand = profile.get("candidate") or {}

    def non_blank(value) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    snapshot: dict[str, Any] = {}

    full_name = non_blank(cand.get("full_name"))
    if full_name:
        snapshot["full_name"] = full_name
        parts = full_name.split(maxsplit=1)
        snapshot["first_name"] = parts[0]
        if len(parts) > 1:
            snapshot["last_name"] = parts[1]

    email = non_blank(cand.get("email"))
    if email:
        snapshot["email"] = email
    phone = non_blank(cand.get("phone"))
    if phone:
        snapshot["phone"] = phone
    linkedin = non_blank(cand.get("linkedin"))
    if linkedin:
        snapshot["linkedin_url"] = linkedin
    github = non_blank(cand.get("github"))
    if github:
        snapshot["github_url"] = github
    subtitle = non_blank(cand.get("subtitle"))
    if subtitle:
        snapshot["current_position"] = subtitle

    location = non_blank(cand.get("location"))
    if location:
        if "," in location:
            city, country = (part.strip() for part in location.split(",", 1))
        else:
            city, country = location, ""
        if city:
            snapshot["city"] = city
        if country:
            snapshot["country"] = country

    # requires_sponsorship: explicit Python bool only. A present truthy
    # string would be an unknown source -> omitted (no coercion).
    if "requires_sponsorship" in cand and isinstance(cand["requires_sponsorship"], bool):
        snapshot["requires_sponsorship"] = cand["requires_sponsorship"]

    return snapshot


def load_evaluations(evaluations_path: Path = Path("data/evaluations.json")) -> list[dict[str, Any]]:
    """Load the evaluations.json file produced by run_evaluate.

    Returns an empty list if the file does not exist.
    """
    if not evaluations_path.exists():
        return []
    with evaluations_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _parse_pipeline_md(pipeline_path: Path) -> dict[str, dict[str, str]]:
    """Parse pipeline.md and return {url: {company, title, location, source}}.

    The pipeline.md format is:
        - [ ] URL | Company | Title [Location]
        - [x] URL | Company | Title [Location]
    """
    if not pipeline_path.exists():
        return {}
    text = pipeline_path.read_text(encoding="utf-8")
    jobs: dict[str, dict[str, str]] = {}
    pattern = r"- \[[ x]\] (https?://\S+)\s*\|\s*([^|]+)\s*\|\s*(.+?)(?:\s*\[([^\]]*)\])?\s*$"
    for match in re.finditer(pattern, text, re.MULTILINE):
        url = match.group(1).strip()
        company = match.group(2).strip()
        title = match.group(3).strip()
        location = match.group(4).strip() if match.group(4) else ""
        jobs[url] = {
            "company": company,
            "title": title,
            "location": location,
            "source": _detect_source(url),
        }
    return jobs


def _detect_source(url: str) -> str:
    """Detect the job source from the URL."""
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower:
        return "indeed"
    if "stepstone" in url_lower:
        return "stepstone"
    if "jobs.siemens.com" in url_lower:
        return "siemens"
    if "boards.greenhouse.io" in url_lower or "greenhouse.io" in url_lower:
        return "greenhouse"
    if "jobs.lever.co" in url_lower:
        return "lever"
    if "myworkdayjobs.com" in url_lower:
        return "workday"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    return "unknown"


def _detect_platform(url: str) -> str:
    """Detect the UAA platform from the URL.

    Returns the platform string for the ApplicationJob.platform field.
    Matches UAA's detect_platform logic in adapters/registry.py.
    """
    url_lower = url.lower()
    if "jobs.siemens.com" in url_lower:
        return "siemens"
    if "boards.greenhouse.io" in url_lower or "greenhouse.io" in url_lower:
        return "greenhouse"
    if "jobs.lever.co" in url_lower:
        return "lever"
    if "myworkdayjobs.com" in url_lower:
        return "workday"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    if "linkedin.com/jobs" in url_lower:
        return "linkedin_easy_apply"
    return "unknown"


# Query keys to strip, case-insensitive (identical to UAA core/identity.py).
_TRACKING_QUERY_KEYS: frozenset[str] = frozenset(
    {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid"}
)
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def _is_tracking_key(key: str) -> bool:
    """Return True if ``key`` is a tracking query parameter (case-insensitive)."""
    lower = key.lower()
    if lower.startswith("utm_"):
        return True
    return lower in _TRACKING_QUERY_KEYS


def canonicalize_url(url: str) -> str:
    """Return the canonical form of ``url``, mirroring UAA core/identity.py.

    - Lowercases scheme and host (and nothing else — path and kept query
      keys keep their original case, e.g. ``jobId``).
    - Preserves URL userinfo exactly.
    - Removes the fragment and default ports (80 for http, 443 for https).
    - Removes a trailing slash except at the host root.
    - Removes ``utm_*`` and tracking query keys case-insensitively without
      altering any other key; remaining pairs are sorted by key then value.

    Raises:
        ValueError: If ``url`` is not an HTTP or HTTPS URL (UAA would reject
            the row, so the exporter skips these with ``invalid_url``).
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"URL must be HTTP or HTTPS, got scheme {scheme!r}")

    host = parts.hostname or ""
    host = host.lower()

    # Remove default port.
    port = parts.port
    netloc = host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    # Preserve userinfo if present.
    if parts.username:
        userinfo: str = parts.username
        if parts.password:
            userinfo = f"{userinfo}:{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    # Path: remove trailing slash except at host root.
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Query: drop tracking keys (case-insensitive match, original case kept),
    # then sort by (key, value) — identical to UAA.
    if parts.query:
        raw_pairs = parse_qsl(parts.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in raw_pairs if not _is_tracking_key(k)]
        filtered.sort(key=lambda pair: (pair[0], pair[1]))
        query = urlencode(filtered)
    else:
        query = ""

    # Fragment is always dropped.
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_external_job_id(value: Any) -> str | None:
    """Normalize a raw external job ID at the JobHunter/UAA boundary.

    Policy (documented in the module docstring and README):

    - ``str`` (non-boolean): returned stripped; a whitespace-only string
      yields ``None`` (absent).
    - ``int`` (non-boolean): returned as ``str(value)`` — ``0`` becomes the
      valid ID ``"0"`` (never lost via truthiness); ``512492`` -> ``"512492"``
      exactly as UAA's lax ``str`` coercion would.
    - ``None``: ``None`` (absent).
    - Anything else (``bool``, ``dict``, ``list``, ``float``): ``None``
      (absent). A malformed ID never crashes the export — it degrades to
      canonical-URL identity.

    Returns:
        A stripped, non-empty string, or ``None`` when the value must be
        treated as absent.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int):
        return str(value)
    return None


def _compute_application_id(platform: str, external_job_id: Any, url: str) -> str:
    """Compute the deterministic application_id (mirror of UAA identity.py).

    Identity source is ``platform + ":" + external_id`` when BOTH are set and
    the external id is a normalized, non-empty string (whitespace-only or
    unsupported values fall back to the canonical URL). The raw external id
    is normalized first via :func:`_normalize_external_job_id`, so an integer
    (e.g. 512492 or 0) produces the same identity as its string form — never
    an ``AttributeError``.

    Returns:
        Lowercase SHA-256 hexdigest string.

    Raises:
        ValueError: If the URL scheme is not HTTP(S) (propagated from
            :func:`canonicalize_url`, including when an external id is set —
            UAA validates the URL regardless of the id).
    """
    import hashlib

    normalized_id = _normalize_external_job_id(external_job_id)
    if platform and normalized_id:
        identity_source = f"{platform}:{normalized_id}"
    else:
        identity_source = canonicalize_url(url)
    return hashlib.sha256(identity_source.encode("utf-8")).hexdigest()


def _skip_record(url: str, company: str, title: str, reason: str, detail: str) -> dict[str, Any]:
    """Build one structured skip-record for the export summary."""
    return {
        "url": url,
        "company": company,
        "title": title,
        "reason": reason,
        "detail": detail,
    }


def build_queue_entries(
    evaluations: list[dict[str, Any]],
    pipeline_jobs: dict[str, dict[str, str]],
    profile_snapshot: dict[str, Any],
    threshold: float = 3.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the queue rows plus the structured list of skipped jobs.

    Filters applied in this fixed, deterministic order per evaluation:

    1. ``success`` must be truthy                       -> ``evaluation_failed``
    2. a ``url`` must be present                        -> ``missing_url``
    3. the URL must be HTTP(S) with a hostname          -> ``invalid_url``
    4. ``global_score`` must be numeric                 -> ``invalid_score``
    5. ``global_score >= threshold``                    -> ``below_threshold``
    6. recommendation must be empty or ``apply``        -> ``not_recommended``
    7. the URL must be new                              -> ``duplicate_url``
    8. company and title must be present                -> ``missing_required_fields``
    9. ``cv_pdf`` and ``cover_letter_pdf`` values       -> ``missing_documents``
    10. both PDFs must exist on disk                    -> ``document_not_found``
    11. the computed ``application_id`` must be new     -> ``duplicate_application_id``

    Evaluations are processed in a deterministic order (sorted by URL/title/
    company) and the returned rows are sorted by ``application_id``, so
    identical input always yields identical output — the ordering of
    ``evaluations.json`` does not matter.

    Args:
        evaluations: List of evaluation dicts from evaluations.json.
        pipeline_jobs: {url: {company, title, location, source}} from
            pipeline.md.
        profile_snapshot: The candidate profile snapshot to embed in metadata.
        threshold: Minimum score to include.

    Returns:
        ``(rows, skipped)`` where ``rows`` is a list of ApplicationJob-
        compatible dicts (no duplicate URLs and no duplicate application_ids)
        and ``skipped`` is a list of ``_skip_record`` dicts.
    """
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()

    # Deterministic processing order independent of input ordering.
    ordered = sorted(
        evaluations,
        key=lambda e: (
            str(e.get("url", "")),
            str(e.get("title", "")),
            str(e.get("company", "")),
        ),
    )

    for evaluation in ordered:
        # 1. Successful evaluation required.
        if not evaluation.get("success"):
            skipped.append(
                _skip_record(
                    str(evaluation.get("url", "")),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    EVALUATION_FAILED,
                    str(evaluation.get("error", "evaluation did not succeed")),
                )
            )
            continue

        # 2. URL required (it is the fallback identity source).
        url = evaluation.get("url")
        if not url:
            skipped.append(
                _skip_record(
                    "",
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    MISSING_URL,
                    "evaluation has no url field",
                )
            )
            continue

        # 3. URL must be HTTP(S) with a hostname — UAA rejects anything else
        # (ApplicationJob._validate_url), so such a job must never be emitted.
        from urllib.parse import urlsplit

        url_parts = urlsplit(url)
        if url_parts.scheme.lower() not in ("http", "https") or not url_parts.hostname:
            skipped.append(
                _skip_record(
                    str(url),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    INVALID_URL,
                    f"url must be HTTP(S) with a hostname, got {str(url)!r}",
                )
            )
            continue

        # 4. Score must be numeric.
        score = evaluation.get("global_score", 0)
        try:
            score_float = float(score)
        except (ValueError, TypeError):
            skipped.append(
                _skip_record(
                    str(url),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    INVALID_SCORE,
                    f"global_score is not numeric: {score!r}",
                )
            )
            continue

        # 5. Above threshold.
        if score_float < threshold:
            skipped.append(
                _skip_record(
                    str(url),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    BELOW_THRESHOLD,
                    f"score {score_float} < threshold {threshold}",
                )
            )
            continue

        # 6. Only jobs recommended for apply are eligible (skip_german,
        # skip, etc. are excluded under the current contract).
        recommendation = evaluation.get("recommendation", "")
        if recommendation and str(recommendation).strip().lower() != "apply":
            skipped.append(
                _skip_record(
                    str(url),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    NOT_RECOMMENDED,
                    f"recommendation is {str(recommendation)!r}, not 'apply'",
                )
            )
            continue

        # 7. No duplicate URL in one export.
        if url in seen_urls:
            skipped.append(
                _skip_record(
                    str(url),
                    str(evaluation.get("company", "")),
                    str(evaluation.get("title", "")),
                    DUPLICATE_URL,
                    "same url already exported once in this queue",
                )
            )
            continue
        seen_urls.add(url)

        # Merge pipeline.md info (for location/source) with evaluation info.
        pipeline_info = pipeline_jobs.get(url, {})
        company = evaluation.get("company") or pipeline_info.get("company") or ""
        title = evaluation.get("title") or pipeline_info.get("title") or ""

        # 8. Required identity fields present (UAA requires non-empty
        # company/title). Never fabricate "Unknown".
        if not company or not title:
            missing = [name for name, value in (("company", company), ("title", title)) if not value]
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    MISSING_REQUIRED_FIELDS,
                    "missing field(s): " + ", ".join(missing),
                )
            )
            continue

        cv_pdf = evaluation.get("cv_pdf_path") or evaluation.get("cv_path")
        cover_pdf = evaluation.get("cover_letter_pdf_path") or evaluation.get("cover_letter_path")

        # 9. Both documents must be referenced.
        if not cv_pdf or not cover_pdf:
            missing = [name for name, value in (("cv_pdf", cv_pdf), ("cover_letter_pdf", cover_pdf)) if not value]
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    MISSING_DOCUMENTS,
                    "missing document path(s): " + ", ".join(missing),
                )
            )
            continue

        # Resolve absolute paths.
        cv_pdf_abs = str(Path(cv_pdf).resolve())
        cover_pdf_abs = str(Path(cover_pdf).resolve())

        # 10. Documents must exist on disk.
        if not Path(cv_pdf_abs).exists():
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    DOCUMENT_NOT_FOUND,
                    f"cv_pdf not found: {cv_pdf_abs}",
                )
            )
            continue
        if not Path(cover_pdf_abs).exists():
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    DOCUMENT_NOT_FOUND,
                    f"cover_letter_pdf not found: {cover_pdf_abs}",
                )
            )
            continue

        location = evaluation.get("location") or pipeline_info.get("location", "")
        source = pipeline_info.get("source", _detect_source(url))
        platform = _detect_platform(url)

        # Application-target quality (additive metadata; never changes
        # eligibility — see utils.target_quality.is_pilot_eligible for the
        # pilot-selection rule). Prefer a scanner-attached quality when the
        # pipeline record carries one.
        from utils.target_quality import classify_target_quality

        target_quality = (
            pipeline_info.get("target_quality")
            or classify_target_quality(url, source)
        )

        # External-ID normalization at the JobHunter/UAA boundary. The raw
        # value may be an integer (e.g. 512492 or 0), a string, or an
        # unsupported type; _normalize_external_job_id turns it into a
        # stripped non-empty string or None (never a crash, never a thrown
        # id that UAA would reject). ``job_id`` is the fallback source only
        # when ``external_job_id`` is entirely absent.
        raw_external_job_id = evaluation.get("external_job_id")
        if raw_external_job_id is None:
            raw_external_job_id = evaluation.get("job_id")
        external_job_id = _normalize_external_job_id(raw_external_job_id)

        try:
            application_id = _compute_application_id(platform, external_job_id, url)
        except ValueError as exc:
            # Defense in depth: any URL the identity logic refuses (e.g. a
            # scheme that slipped through) must skip, never emit a bad row.
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    INVALID_URL,
                    f"cannot compute application_id: {exc}",
                )
            )
            continue

        # 11. No duplicate application_id in one export.
        if application_id in seen_ids:
            skipped.append(
                _skip_record(
                    str(url),
                    company,
                    title,
                    DUPLICATE_APPLICATION_ID,
                    f"application_id {application_id} already exported once in this queue",
                )
            )
            continue
        seen_ids.add(application_id)

        date_posted = evaluation.get("date_posted")
        if date_posted:
            # Normalize to YYYY-MM-DD if it's a datetime.
            if isinstance(date_posted, str) and len(date_posted) >= 10:
                date_posted = date_posted[:10]
            else:
                date_posted = None

        # Build the markdown source paths if available.
        cv_md = evaluation.get("cv_path")
        cover_md = evaluation.get("cover_letter_path")
        documents: dict[str, str] | None = None
        if cv_md or cover_md:
            documents = {}
            if cv_md:
                documents["cv_md"] = str(Path(cv_md).resolve())
            if cover_md:
                documents["cover_letter_md"] = str(Path(cover_md).resolve())

        row: dict[str, Any] = {
            "application_id": application_id,
            "platform": platform,
            "source": source,
            "company": company,
            "title": title,
            "url": url,
            "location": location,
            "job_description": evaluation.get("description", ""),
            "score": score_float,
            "verdict": "apply",
            "cv_pdf": cv_pdf_abs,
            "cover_letter_pdf": cover_pdf_abs,
            "status": "ready_to_apply",
            "external_job_id": external_job_id,
            "date_posted": date_posted,
            "evaluated_at": evaluation.get("evaluated_at"),
            "tailored_at": evaluation.get("tailored_at"),
            "evaluation_reason": evaluation.get("recommendation_reason")
            or evaluation.get("reason", "")
            or evaluation.get("reasoning", ""),
            "german_filter_result": evaluation.get("german_level_required", ""),
            "documents": documents,
            "metadata": {
                "candidate_profile": profile_snapshot,
                "score_breakdown": evaluation.get("scores"),
                "target_quality": target_quality,
            },
        }
        rows.append(row)

    # Deterministic output ordering (byte-stable for identical inputs).
    rows.sort(key=lambda r: (r["application_id"], r["url"]))

    return rows, skipped


def build_queue_rows(
    evaluations: list[dict[str, Any]],
    pipeline_jobs: dict[str, dict[str, str]],
    profile_snapshot: dict[str, Any],
    threshold: float = 3.5,
) -> list[dict[str, Any]]:
    """Build a list of ApplicationJob-compatible queue rows.

    Backward-compatible wrapper around :func:`build_queue_entries` that
    returns only the rows (callers that need skip reasons use
    ``build_queue_entries`` / ``export_queue``).

    Filters:
    - Only jobs with success=True
    - score >= threshold
    - recommendation is empty or "apply"
    - Has cv_pdf_path and cover_letter_pdf_path that exist on disk

    Args:
        evaluations: List of evaluation dicts from evaluations.json.
        pipeline_jobs: {url: {company, title, location, source}} from
            pipeline.md.
        profile_snapshot: The candidate profile snapshot to embed in metadata.
        threshold: Minimum score to include.

    Returns:
        A list of dicts, each compatible with UAA's ApplicationJob contract,
        deterministically ordered and free of duplicate URLs / application_ids.
    """
    rows, _skipped = build_queue_entries(evaluations, pipeline_jobs, profile_snapshot, threshold)
    return rows


def _atomic_write_jsonl(output_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` to ``output_path`` atomically.

    A uniquely-named temporary file is created in the destination directory
    (same filesystem, so the rename is atomic), flushed and closed, then
    ``os.replace`` swops it over the final path. Readers can never observe a
    partially-written queue file, and any failure leaves the previous
    completed queue untouched while the temp file is cleaned up.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent),
        prefix=output_path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, output_path)
    except BaseException:
        # A failed publish must never leave a stray temp file behind.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def export_queue(
    output_path: Path | None = None,
    evaluations_path: Path = Path("data/evaluations.json"),
    pipeline_path: Path = Path("data/pipeline.md"),
    profile_path: Path = Path("config/profile.yml"),
    threshold: float | None = None,
    freshness_check: bool = True,
) -> dict[str, Any]:
    """Export the application_queue.jsonl file.

    This is the main entry point for the queue handoff. It:
    1. Loads evaluations.json, pipeline.md, and profile.yml.
    2. Builds queue rows for the jobs that pass all eligibility filters
       (with structured skip reasons for every excluded job).
    3. Revalidates each row's application target where a deterministic
       public lookup exists (unless ``freshness_check`` is False);
       positively expired targets are excluded with reason
       ``application_expired``. Transient/unknown lookup states never
       block export.
    4. Atomically writes them to output_path as JSONL (never exposing a
       partial file; the previous queue survives a failed write).
    5. Returns a summary dict with exported/skipped counts and reasons.

    The export is deterministic and idempotent: identical input produces
    byte-stable output, and re-running replaces (never appends) the file.

    Args:
        output_path: Where to write application_queue.jsonl. If None (and if
            not resolved from config/CLI), reads ``profile.yml ->
            queue_export.output_path``; falls back to
            ``data/application_queue.jsonl``.
        evaluations_path: Path to evaluations.json.
        pipeline_path: Path to pipeline.md.
        profile_path: Path to profile.yml.
        threshold: Minimum score to include. If None, reads from profile.yml
            (evaluation.auto_cv_threshold, default 3.5).
        freshness_check: Revalidate application targets before export
            (default True). Disable only for offline/hermetic use.

    Returns:
        A summary dict with counts and structured skip reasons.
    """
    profile = load_profile(profile_path)
    if threshold is None:
        threshold = profile.get("evaluation", {}).get("auto_cv_threshold", 3.5)

    if output_path is None:
        configured = profile.get("queue_export", {}).get("output_path")
        output_path = Path(configured) if configured else Path("data/application_queue.jsonl")
    output_path = Path(output_path)

    profile_snapshot = extract_candidate_profile_snapshot(profile)
    evaluations = load_evaluations(evaluations_path)
    pipeline_jobs = _parse_pipeline_md(pipeline_path)

    rows, skipped = build_queue_entries(evaluations, pipeline_jobs, profile_snapshot, threshold)

    if freshness_check and rows:
        from utils.freshness import filter_fresh_rows

        rows, freshness_skipped = filter_fresh_rows(rows)
        skipped.extend(freshness_skipped)

    # Atomic publish: replace the finished queue atomically.
    _atomic_write_jsonl(output_path, rows)

    skipped_reasons: dict[str, int] = {}
    for record in skipped:
        reason = record["reason"]
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    summary = {
        "total_evaluations": len(evaluations),
        "exported": len(rows),
        "skipped": len(skipped),
        "threshold": threshold,
        "skipped_reasons": skipped_reasons,
        "skipped_jobs": skipped,
        "output_path": str(output_path),
    }
    logger.info(
        "export complete: %d of %d evaluations exported to %s (threshold=%s, skipped=%d)",
        len(rows),
        len(evaluations),
        output_path,
        threshold,
        len(skipped),
    )
    return summary


__all__ = [
    "export_queue",
    "build_queue_entries",
    "build_queue_rows",
    "load_profile",
    "extract_candidate_profile_snapshot",
    "load_evaluations",
    "canonicalize_url",
    "APPLICATION_EXPIRED",
    "INVALID_URL",
    "SKIP_REASONS",
]