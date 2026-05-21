"""
JobSpy scanner — wraps python-jobspy to search LinkedIn, Indeed, and Google Jobs.

Returns normalized JobPosting objects from all configured search queries.
Handles rate limits and errors per-site gracefully.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

# numpy must be imported BEFORE jobspy to suppress floating-point
# exception traps that cause "longdouble infinity to integer" crashes
# on Python 3.14 + numpy 2.4 (https://github.com/numpy/numpy/issues/...)
try:
    import numpy as np
    np.seterr(all="ignore")
except ImportError:
    pass

from .base import BaseScanner, JobPosting, ScanResult

logger = logging.getLogger(__name__)


class JobSpyScanner(BaseScanner):
    """Scanner using python-jobspy for LinkedIn, Indeed, Google Jobs."""

    def __init__(self, config: dict):
        super().__init__(config, name="jobspy")
        self.searches = [
            s for s in config.get("jobspy_searches", [])
            if s.get("enabled", True)
        ]

    async def scan(self) -> ScanResult:
        """Execute all configured JobSpy searches."""
        result = self._make_result()

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
                    result.errors.append(error_msg)
                    self.logger.error(error_msg)

                # Delay between searches to avoid rate limits
                await asyncio.sleep(2.0)

        return self._finish_result(result)

    def _run_search(self, search_config: dict) -> list[JobPosting]:
        """Run a single JobSpy search (synchronous, runs in thread)."""
        term = search_config.get("term", "Working Student")
        location = search_config.get("location", "Erlangen, Germany")

        try:
            return self._do_search(search_config)
        except BaseException as e:
            self.logger.warning(
                f"JobSpy search '{term}' in '{location}' failed: "
                f"{type(e).__name__}: {e}"
            )
            return []

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
