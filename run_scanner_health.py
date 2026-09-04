"""run_scanner_health.py — machine-readable scanner health report.

Probes each enabled non-Siemens scanner with a small bounded request set
and prints one JSON document:

    python run_scanner_health.py --json

Output per company: company, method, status, jobs_found,
direct_ats_candidates, error_category, error (sanitized — no absolute
paths, no secrets).

Siemens is out of scope (dedicated workflow) and is never probed.

Exit code 0 always (health reporting itself must not fail); consumers
decide readiness from the per-company statuses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from scanners.base import ScanResult, ScanStatus, classify_exception
from utils.target_quality import DIRECT_ATS, classify_target_quality
from utils.utf8_logging import get_utf8_stream_handler

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[get_utf8_stream_handler(sys.stdout)],
)
logger = logging.getLogger("scanner-health")


def _sanitize(text: str) -> str:
    """Strip absolute paths and secrets from an error message."""
    home = os.path.expanduser("~")
    cleaned = text.replace(home, "~")
    # Never leak anything shaped like a key/token.
    for token in ("sk-or-", "AIza"):
        idx = cleaned.find(token)
        while idx != -1:
            end = idx
            while end < len(cleaned) and cleaned[end] not in (" ", '"', "'", "\n"):
                end += 1
            cleaned = cleaned[:idx] + token + "***" + cleaned[end:]
            idx = cleaned.find(token)
    return cleaned[:500]


def _direct_ats_count(jobs: list) -> int:
    """Count jobs whose URL is a direct ATS posting page."""
    count = 0
    for job in jobs:
        url = job.url if hasattr(job, "url") else job.get("url", "")
        source = job.source if hasattr(job, "source") else job.get("source", "")
        if classify_target_quality(url, source) == DIRECT_ATS:
            count += 1
    return count


def _entry(
    company: str,
    method: str,
    result: ScanResult | None,
    note: str = "",
) -> dict:
    """Build one health entry from a ScanResult (or a skip note)."""
    if result is None:
        return {
            "company": company,
            "method": method,
            "status": "SKIPPED",
            "jobs_found": 0,
            "direct_ats_candidates": 0,
            "error_category": None,
            "error": _sanitize(note),
        }
    return {
        "company": company,
        "method": method,
        "status": result.status,
        "jobs_found": result.job_count,
        "direct_ats_candidates": _direct_ats_count(result.jobs),
        "error_category": result.error_category,
        "error": _sanitize(result.errors[0] if result.errors else ""),
    }


async def probe_workday_company(config: dict, company_name: str) -> dict:
    """Probe one workday/direct-api company with its configured terms."""
    from scanners.workday import DirectAPIScanner

    scanner = DirectAPIScanner(config)
    endpoint = next(
        (e for e in scanner.companies if e.company_name == company_name), None
    )
    if endpoint is None:
        return _entry(
            company_name, "workday", None, "no verified direct API endpoint"
        )
    result = scanner._make_result()
    try:
        jobs = await scanner._scan_company(endpoint, result)
        result.jobs.extend(jobs)
    except Exception as e:  # noqa: BLE001 — recorded, never raised
        result.note_error(f"{company_name}: {e}", classify_exception(e))
    result.finalize()
    return _entry(company_name, "workday", result)


async def probe_jobspy(term: str, location: str) -> dict:
    """Probe JobSpy once with a small bounded search."""
    try:
        from scanners.jobspy_scanner import JobSpyScanner
    except Exception as e:  # noqa: BLE001 — import-time crash (e.g. numpy)
        result = ScanResult(scanner_name="jobspy")
        result.note_error(f"JobSpy unavailable: {e}", classify_exception(e))
        result.status = ScanStatus.DEPENDENCY_FAILURE.value
        return _entry("jobspy", "jobspy", result)

    scanner = JobSpyScanner({"jobspy_searches": []})
    search_config = {
        "term": term,
        "location": location,
        "sites": ["indeed"],
        "results_wanted": 5,
    }
    loop = asyncio.get_running_loop()
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as executor:
            jobs = await loop.run_in_executor(
                executor, scanner._run_search, search_config
            )
        result = scanner._make_result()
        result.jobs.extend(jobs)
        result.finalize()
        entry = _entry("jobspy", "jobspy", result)
        entry["company"] = f"jobspy probe ({term} / {location})"
        return entry
    except Exception as e:  # noqa: BLE001 — recorded, never raised
        result = scanner._make_result()
        result.note_error(f"JobSpy probe failed: {e}", classify_exception(e))
        result.finalize()
        entry = _entry("jobspy", "jobspy", result)
        entry["company"] = f"jobspy probe ({term} / {location})"
        return entry


async def probe_websearch(config: dict) -> list[dict]:
    """Run the websearch scanner once across all configured companies."""
    from scanners.websearch_scanner import WebSearchScanner

    scanner = WebSearchScanner(config)
    by_company: dict[str, list] = {}
    result = await scanner.scan()
    for job in result.jobs:
        by_company.setdefault(job.company, []).append(job)

    entries = []
    probed = set()
    for site in scanner.sites:
        jobs = by_company.get(site.company_name, [])
        fake = ScanResult(scanner_name="websearch")
        fake.jobs.extend(jobs)
        # Carry scanner-level errors mentioning this company, if any.
        for err in result.errors:
            if site.company_name.lower() in err.lower():
                fake.note_error(err, result.error_category)
        fake.finalize()
        # If nothing found and no error recorded, it is a valid empty page.
        entries.append(_entry(site.company_name, "websearch", fake))
        probed.add(site.company_name)
    if result.errors and not entries:
        entries.append(_entry("websearch", "websearch", result))
    return entries


async def run_health() -> dict:
    """Probe all enabled non-Siemens scanners and return the report."""
    load_dotenv()
    with open(Path("config/portals.yml"), "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    companies: list[dict] = []
    jobspy_needed = False
    for company in config.get("tracked_companies", []):
        if not company.get("enabled", True):
            continue
        if company.get("name", "").lower() == "siemens":
            continue  # Out of scope — dedicated workflow, never probed.
        method = company.get("scan_method", "")
        if method in ("workday", "direct-api"):
            companies.append(await probe_workday_company(config, company["name"]))
        elif method == "jobspy":
            jobspy_needed = True
        # websearch companies are covered by the single websearch probe below.

    if jobspy_needed:
        companies.append(await probe_jobspy("Werkstudent", "Erlangen, Germany"))

    companies.extend(await probe_websearch(config))

    return {
        "companies": companies,
        "note": "siemens: out of scope (dedicated workflow), not probed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scanner health report")
    parser.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    args = parser.parse_args()

    report = asyncio.run(run_health())
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for entry in report["companies"]:
            print(
                f"{entry['company']}: method={entry['method']} "
                f"status={entry['status']} jobs={entry['jobs_found']} "
                f"direct_ats={entry['direct_ats_candidates']} "
                f"category={entry['error_category']}"
            )
            if entry["error"]:
                print(f"    error: {entry['error']}")
        print(f"note: {report['note']}")


if __name__ == "__main__":
    main()
