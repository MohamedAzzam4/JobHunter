"""Queue exporter — writes application_queue.jsonl for UniversalAutoApplier.

Per ROADMAP.md WP 1.2 and DATA_CONTRACTS.md:

- Export only jobs that:
  - passed evaluation
  - have verdict ``apply``
  - are above threshold
  - have tailored CV and cover letter artifacts (PDFs)
- Do not export rejected, stale, duplicate, or already-applied jobs.
- Output format: one JSON object per line (JSONL).
- Export is deterministic and idempotent.

The exporter reads from:
- ``data/evaluations.json`` (the evaluator's output records)
- ``data/pipeline.md`` (the scanner's job list with URLs/companies/titles)
- ``config/profile.yml`` (the candidate profile snapshot)

And writes:
- ``data/application_queue.jsonl`` (one ApplicationJob per line)

Each queue row contains the full ApplicationJob contract fields plus a
candidate profile snapshot under ``metadata.candidate_profile`` so that
UniversalAutoApplier can fill forms without an empty CandidateProfile().

The exporter is intentionally pure (no network, no LLM calls) so it can
run after the evaluate phase as a separate step.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("queue_exporter")


def load_profile(profile_path: Path = Path("config/profile.yml")) -> dict[str, Any]:
    """Load the candidate profile YAML.

    Returns an empty dict if the file does not exist.
    """
    if not profile_path.exists():
        return {}
    with profile_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def extract_candidate_profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    """Extract a flat candidate profile snapshot for UAA.

    UAA's CandidateProfile model has fields: first_name, last_name,
    full_name, email, phone, linkedin_url, city, country,
    requires_sponsorship, work_authorization, years_of_experience,
    current_position, website, github_url.

    We map from JobHunter's profile.yml structure.
    """
    cand = profile.get("candidate", {})
    full_name = cand.get("full_name", "")
    # Split full_name into first/last (best effort).
    parts = full_name.split(maxsplit=1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""

    location = cand.get("location", "")
    # Split "Erlangen, Germany" into city/country.
    if "," in location:
        city, country = (s.strip() for s in location.split(",", 1))
    else:
        city, country = location, ""

    return {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": cand.get("email", ""),
        "phone": cand.get("phone", ""),
        "linkedin_url": cand.get("linkedin", ""),
        "github_url": cand.get("github", ""),
        "city": city,
        "country": country,
        "website": "",
        "requires_sponsorship": False,
        "work_authorization": "",
        "years_of_experience": None,
        "current_position": cand.get("subtitle", ""),
    }


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


def _compute_application_id(platform: str, external_job_id: str | None, url: str) -> str:
    """Compute the deterministic application_id.

    This mirrors UAA's core/identity.py compute_application_id exactly:
    - If platform and external_job_id both exist: identity_source = platform + ":" + external_job_id
    - Otherwise: identity_source = canonical URL
    - application_id = sha256(identity_source).hexdigest()
    """
    import hashlib
    from urllib.parse import urlsplit, parse_qsl, urlencode

    if platform and external_job_id:
        identity_source = f"{platform}:{external_job_id.strip()}"
    else:
        # Canonical URL (mirror UAA's logic).
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        path = parts.path or ""
        # Remove trailing slash except at host root.
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        # Remove default port.
        port = parts.port
        netloc = host
        if port and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        # Filter query keys.
        drop_prefixes = ("utm_",)
        drop_keys = {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid"}
        kept: list[tuple[str, str]] = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            kl = k.lower()
            if any(kl.startswith(p) for p in drop_prefixes):
                continue
            if kl in drop_keys:
                continue
            kept.append((kl, v))
        kept.sort(key=lambda kv: (kv[0], kv[1]))
        query = urlencode(kept)
        identity_source = f"{scheme}://{netloc}{path}"
        if query:
            identity_source += f"?{query}"
    return hashlib.sha256(identity_source.encode("utf-8")).hexdigest()


def build_queue_rows(
    evaluations: list[dict[str, Any]],
    pipeline_jobs: dict[str, dict[str, str]],
    profile_snapshot: dict[str, Any],
    threshold: float = 3.5,
) -> list[dict[str, Any]]:
    """Build a list of ApplicationJob-compatible queue rows.

    Filters:
    - Only jobs with success=True
    - score >= threshold
    - verdict == "apply"
    - Has cv_pdf_path and cover_letter_pdf_path

    Args:
        evaluations: List of evaluation dicts from evaluations.json.
        pipeline_jobs: {url: {company, title, location, source}} from pipeline.md.
        profile_snapshot: The candidate profile snapshot to embed in metadata.
        threshold: Minimum score to include.

    Returns:
        A list of dicts, each compatible with UAA's ApplicationJob contract.
    """
    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for evaluation in evaluations:
        if not evaluation.get("success"):
            continue

        score = evaluation.get("global_score", 0)
        try:
            score_float = float(score)
        except (ValueError, TypeError):
            continue
        if score_float < threshold:
            continue

        recommendation = evaluation.get("recommendation", "")
        if recommendation and recommendation != "apply":
            # skip_german, skip, etc.
            continue

        url = evaluation.get("url", "")
        if not url or url in seen_urls:
            continue

        cv_pdf = evaluation.get("cv_pdf_path") or evaluation.get("cv_path")
        cover_pdf = evaluation.get("cover_letter_pdf_path") or evaluation.get("cover_letter_path")
        if not cv_pdf or not cover_pdf:
            logger.warning(
                "Skipping %s (score %s): missing CV or cover letter PDF",
                url[:60],
                score,
            )
            continue

        # Resolve absolute paths.
        cv_pdf_abs = str(Path(cv_pdf).resolve())
        cover_pdf_abs = str(Path(cover_pdf).resolve())

        # Verify files exist.
        if not Path(cv_pdf_abs).exists():
            logger.warning("Skipping %s: cv_pdf does not exist: %s", url[:60], cv_pdf_abs)
            continue
        if not Path(cover_pdf_abs).exists():
            logger.warning("Skipping %s: cover_letter_pdf does not exist: %s", url[:60], cover_pdf_abs)
            continue

        # Merge pipeline.md info (for location/source) with evaluation info.
        pipeline_info = pipeline_jobs.get(url, {})
        company = evaluation.get("company") or pipeline_info.get("company", "Unknown")
        title = evaluation.get("title") or pipeline_info.get("title", "Unknown")
        location = evaluation.get("location") or pipeline_info.get("location", "")
        source = pipeline_info.get("source", _detect_source(url))
        platform = _detect_platform(url)

        external_job_id = evaluation.get("external_job_id") or evaluation.get("job_id")
        application_id = _compute_application_id(platform, external_job_id, url)

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
            or evaluation.get("reason", ""),
            "german_filter_result": evaluation.get("german_level_required", ""),
            "documents": documents,
            "metadata": {
                "candidate_profile": profile_snapshot,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "score_breakdown": evaluation.get("score_breakdown"),
            },
        }
        rows.append(row)
        seen_urls.add(url)

    return rows


def export_queue(
    output_path: Path = Path("data/application_queue.jsonl"),
    evaluations_path: Path = Path("data/evaluations.json"),
    pipeline_path: Path = Path("data/pipeline.md"),
    profile_path: Path = Path("config/profile.yml"),
    threshold: float | None = None,
) -> dict[str, Any]:
    """Export the application_queue.jsonl file.

    This is the main entry point. It:
    1. Loads evaluations.json, pipeline.md, and profile.yml.
    2. Builds queue rows for jobs that pass all filters.
    3. Writes them to output_path as JSONL.
    4. Returns a summary dict.

    The export is idempotent: running it twice produces the same file
    (assuming the inputs don't change). Existing rows are replaced, not
    appended.

    Args:
        output_path: Where to write application_queue.jsonl.
        evaluations_path: Path to evaluations.json.
        pipeline_path: Path to pipeline.md.
        profile_path: Path to profile.yml.
        threshold: Minimum score to include. If None, reads from profile.yml
            (evaluation.auto_cv_threshold, default 3.5).

    Returns:
        A summary dict with counts.
    """
    profile = load_profile(profile_path)
    if threshold is None:
        threshold = profile.get("evaluation", {}).get("auto_cv_threshold", 3.5)

    profile_snapshot = extract_candidate_profile_snapshot(profile)
    evaluations = load_evaluations(evaluations_path)
    pipeline_jobs = _parse_pipeline_md(pipeline_path)

    rows = build_queue_rows(evaluations, pipeline_jobs, profile_snapshot, threshold)

    # Write JSONL (atomic: write to temp then rename).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    tmp_path.replace(output_path)

    summary = {
        "total_evaluations": len(evaluations),
        "exported": len(rows),
        "threshold": threshold,
        "output_path": str(output_path),
    }
    logger.info(
        "export complete: %d of %d evaluations exported to %s (threshold=%s)",
        len(rows),
        len(evaluations),
        output_path,
        threshold,
    )
    return summary


__all__ = [
    "export_queue",
    "build_queue_rows",
    "load_profile",
    "extract_candidate_profile_snapshot",
    "load_evaluations",
]
