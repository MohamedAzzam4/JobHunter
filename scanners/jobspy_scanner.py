"""
JobSpy scanner — wraps python-jobspy to search LinkedIn, Indeed, and Google Jobs.

Returns normalized JobPosting objects from all configured search queries.
Handles rate limits and errors per-site gracefully.
"""

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from .base import (
    BaseScanner,
    JobPosting,
    ScanResult,
    ScanStatus,
    classify_exception,
)

logger = logging.getLogger(__name__)

# Supported runtimes for the JobSpy/pandas/numpy stack. The repo Dockerfile
# pins python:3.12-slim; Python 3.13 is verified working locally. NumPy
# crashes at import time on Python 3.14 ("longdouble infinity to integer"),
# so the scanner must degrade to DEPENDENCY_FAILURE instead of killing the
# whole pipeline (see run_scan.py lazy import + this module's guards).
SUPPORTED_PYTHON_MAJOR_MINOR = ((3, 12), (3, 13))


def _check_runtime() -> str | None:
    """Return a DEPENDENCY_FAILURE reason when unsupported, else None."""
    current = sys.version_info[:2]
    if current not in SUPPORTED_PYTHON_MAJOR_MINOR and current >= (3, 14):
        return (
            f"python {current[0]}.{current[1]} is not supported for the "
            f"JobSpy/numpy stack (supported: "
            + ", ".join(f"{a}.{b}" for a, b in SUPPORTED_PYTHON_MAJOR_MINOR)
            + "); use the documented supported runtime"
        )
    return None


class JobSpyScanner(BaseScanner):
    """Scanner using python-jobspy for LinkedIn, Indeed, Google Jobs."""

    def __init__(self, config: dict):
        super().__init__(config, name="jobspy")
        self.searches = self._load_searches(config)

    @staticmethod
    def _load_searches(config: dict) -> list[dict]:
        """Load search configs, supporting both compact and legacy formats.

        Compact format (terms × locations cross-product):
            jobspy_searches:
              terms: ["Working Student", "Werkstudent"]
              locations: ["Erlangen, Germany", "Munich, Germany"]
              sites: ["indeed", "linkedin", "google"]
              results_wanted: 50
              distance_km: 50

        Legacy format (explicit per-search entries):
            jobspy_searches:
              - term: "Working Student"
                location: "Erlangen, Germany"
                ...
        """
        raw = config.get("jobspy_searches", {})

        # Legacy format: list of dicts
        if isinstance(raw, list):
            return [s for s in raw if s.get("enabled", True)]

        # Compact format: dict with terms + locations
        terms = raw.get("terms", [])
        locations = raw.get("locations", [])
        if not terms or not locations:
            return []

        sites = raw.get("sites", ["indeed", "linkedin", "google"])
        results_wanted = raw.get("results_wanted", 50)
        distance_km = raw.get("distance_km", None)

        searches = []
        for term in terms:
            for location in locations:
                entry = {
                    "term": term,
                    "location": location,
                    "sites": sites,
                    "results_wanted": results_wanted,
                }
                if distance_km:
                    entry["distance_km"] = distance_km
                searches.append(entry)

        return searches

    async def scan(self) -> ScanResult:
        """Execute all configured JobSpy searches."""
        result = self._make_result()

        # Fail fast with a clear category on unsupported runtimes instead of
        # crashing deep inside numpy/jobspy imports.
        runtime_reason = _check_runtime()
        if runtime_reason is not None:
            result.note_error(
                f"JobSpy skipped: {runtime_reason}",
                ScanStatus.DEPENDENCY_FAILURE.value,
            )
            self.logger.error(f"JobSpy skipped: {runtime_reason}")
            return self._finish_result(result)

        if not self.searches:
            self.logger.warning("No jobspy_searches configured in portals.yml")
            return self._finish_result(result)

        # Run jobspy in a thread pool since it's synchronous + blocking
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            for search_config in self.searches:
                try:
                    jobs = await loop.run_in_executor(
                        executor, self._run_search, search_config
                    )
                    result.jobs.extend(jobs)
                except Exception as e:
                    error_msg = (
                        f"JobSpy search '{search_config.get('term', '?')}' "
                        f"in '{search_config.get('location', '?')}': {e}"
                    )
                    result.note_error(error_msg, classify_exception(e))
                    self.logger.error(error_msg)

                # Delay between searches to avoid rate limits
                await asyncio.sleep(2.0)

        return self._finish_result(result)

    def _run_search(self, search_config: dict) -> list[JobPosting]:
        """Run a single JobSpy search (synchronous, runs in thread).

        Failures propagate to :meth:`scan`, which records them with a
        failure category — a failed search is never reported as zero jobs.
        """
        return self._do_search(search_config)

    def _do_search(self, search_config: dict) -> list[JobPosting]:
        """Inner search logic — separated so errors are always caught."""
        try:
            from jobspy import scrape_jobs
        except ImportError:
            raise ImportError(
                "python-jobspy not installed. Run: pip install python-jobspy"
            )

        term = search_config.get("term", "Working Student")
        location = search_config.get("location", "Erlangen, Germany")
        sites = search_config.get("sites", ["indeed", "google"])
        results_wanted = search_config.get("results_wanted", 30)
        distance_km = search_config.get("distance_km", None)

        self.logger.info(
            f"Searching '{term}' in '{location}' on {sites} "
            f"(want {results_wanted} results)"
        )

        kwargs = {
            "site_name": sites,
            "search_term": term,
            "location": location,
            "results_wanted": results_wanted,
            "country_indeed": "Germany",
        }

        # distance parameter (if supported by the jobspy version)
        if distance_km:
            kwargs["distance"] = distance_km

        df = scrape_jobs(**kwargs)

        if df is None or df.empty:
            self.logger.info(f"No results for '{term}' in '{location}'")
            return []

        jobs = []
        for _, row in df.iterrows():
            try:
                # Normalize URL — jobspy sometimes returns NaN
                url = str(row.get("job_url", "")) if row.get("job_url") is not None else ""
                if not url or url == "nan":
                    continue

                title = str(row.get("title", "")) if row.get("title") is not None else ""
                # JobSpy uses "company" not "company_name"
                company = str(row.get("company", "")) if row.get("company") is not None else ""
                if company == "nan":
                    company = ""
                location_str = str(row.get("location", "")) if row.get("location") is not None else ""
                description = str(row.get("description", "")) if row.get("description") is not None else ""
                date_posted = str(row.get("date_posted", "")) if row.get("date_posted") is not None else ""
                if date_posted == "NaT" or date_posted == "nan":
                    date_posted = ""
                salary = ""

                # Try to get salary info — guard against numpy inf/NaN
                try:
                    import math
                    min_sal = row.get("min_amount")
                    max_sal = row.get("max_amount")
                    if (min_sal is not None and max_sal is not None
                            and not math.isnan(float(min_sal))
                            and not math.isinf(float(min_sal))):
                        currency = row.get("currency", "EUR")
                        salary = f"{currency} {min_sal}-{max_sal}"
                except (ValueError, TypeError, OverflowError):
                    salary = ""

                # Determine source site
                site = str(row.get("site", "jobspy"))

                jobs.append(JobPosting(
                    title=title,
                    url=url,
                    company=company,
                    location=location_str,
                    date_posted=date_posted,
                    source=f"jobspy-{site}",
                    description=description[:5000] if description else "",
                    salary=salary,
                ))
            except Exception as e:
                self.logger.debug(f"Skipping row due to error: {e}")
                continue

        self.logger.info(
            f"Got {len(jobs)} results for '{term}' in '{location}'"
        )
        return jobs
