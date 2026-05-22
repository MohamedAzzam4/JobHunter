"""
Direct API scanner for companies using Workday, SmartRecruiters, or similar
public/semi-public career APIs.

IMPORTANT: Siemens does NOT use Workday — they use Eightfold.ai which has
no public API. Siemens jobs are discovered via JobSpy (LinkedIn/Indeed)
instead. See portals.yml for the correct scan_method per company.

Supported providers:
- workday: POST to /wday/cxs/{tenant}/{board}/jobs
- smartrecruiters: GET from /api/v1/search
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from .base import BaseScanner, JobPosting, ScanResult

logger = logging.getLogger(__name__)

# Known API endpoints — discovered by inspecting network traffic
# Only include companies with VERIFIED, working endpoints
# Note: Adidas/Puma use Eightfold.ai (no direct API support) — covered by JobSpy
KNOWN_ENDPOINTS = {
    "bosch": {
        "api_url": "https://careers.smartrecruiters.com/BoschGroup",
        "base_url": "https://www.bosch.com/careers/",
        "provider": "smartrecruiters",
    },
}

FETCH_TIMEOUT = 20
MAX_PAGES = 5
PAGE_SIZE = 20


@dataclass
class APIEndpoint:
    """A company API endpoint."""
    company_name: str
    api_url: str
    base_url: str
    provider: str  # "workday", "smartrecruiters", "eightfold"


class DirectAPIScanner(BaseScanner):
    """Scanner for companies with known public/semi-public APIs.
    
    This scanner only handles companies where we have a verified API endpoint.
    Companies without API access (like Siemens/Eightfold) are handled by
    JobSpy via LinkedIn/Indeed/Google instead.
    """

    def __init__(self, config: dict):
        super().__init__(config, name="direct-api")
        self.companies = self._load_companies()

    def _load_companies(self) -> list[APIEndpoint]:
        """Load companies with known API endpoints from config."""
        endpoints = []
        tracked = self.config.get("tracked_companies", [])

        for company in tracked:
            if not company.get("enabled", True):
                continue
            if company.get("scan_method") not in ("workday", "direct-api"):
                continue

            name_lower = company["name"].lower()

            if name_lower in KNOWN_ENDPOINTS:
                known = KNOWN_ENDPOINTS[name_lower]
                endpoints.append(APIEndpoint(
                    company_name=company["name"],
                    api_url=known["api_url"],
                    base_url=known["base_url"],
                    provider=known["provider"],
                ))
            else:
                self.logger.info(
                    f"No direct API for '{company['name']}' — "
                    f"will be covered by JobSpy scanner instead."
                )

        self.logger.info(f"Loaded {len(endpoints)} direct-API companies")
        return endpoints

    async def scan(self) -> ScanResult:
        """Scan all companies with direct API access."""
        result = self._make_result()

        for endpoint in self.companies:
            try:
                jobs = await self._scan_company(endpoint)
                result.jobs.extend(jobs)
            except Exception as e:
                error_msg = f"{endpoint.company_name}: {e}"
                result.errors.append(error_msg)
                self.logger.error(f"Error scanning {error_msg}")

        return self._finish_result(result)

    async def _scan_company(self, endpoint: APIEndpoint) -> list[JobPosting]:
        """Scan a single company via its API."""
        search_terms = self._get_search_terms(endpoint.company_name)
        all_jobs = []

        for search_term in search_terms:
            try:
                if endpoint.provider == "workday":
                    jobs = await self._scan_workday(endpoint, search_term)
                elif endpoint.provider == "smartrecruiters":
                    jobs = await self._scan_smartrecruiters(endpoint, search_term)
                else:
                    # For unsupported providers, skip gracefully
                    self.logger.info(
                        f"{endpoint.company_name}: provider '{endpoint.provider}' "
                        f"not directly supported — skipping API scan"
                    )
                    continue

                all_jobs.extend(jobs)
            except httpx.TimeoutException:
                self.logger.warning(
                    f"{endpoint.company_name}: timeout on '{search_term}'"
                )
            except httpx.ConnectError:
                self.logger.warning(
                    f"{endpoint.company_name}: connection failed (site may be geo-blocked)"
                )
            except Exception as e:
                self.logger.warning(
                    f"{endpoint.company_name}: error on '{search_term}': {e}"
                )

            await asyncio.sleep(1)  # Be polite between searches

        self.logger.info(f"{endpoint.company_name}: found {len(all_jobs)} jobs via API")
        return all_jobs

    async def _scan_workday(self, endpoint: APIEndpoint, search_text: str) -> list[JobPosting]:
        """Scan a Workday career portal."""
        all_jobs = []
        offset = 0

        for _ in range(MAX_PAGES):
            payload = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
            }

            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.post(
                    endpoint.api_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    },
                )

                if resp.status_code != 200:
                    self.logger.warning(
                        f"{endpoint.company_name}: HTTP {resp.status_code} "
                        f"(offset={offset})"
                    )
                    break

                data = resp.json()
                total = data.get("total", 0)
                postings = data.get("jobPostings", [])

                for p in postings:
                    title = p.get("title", "")
                    external_path = p.get("externalPath", "")
                    url = f"{endpoint.base_url}{external_path}" if external_path else ""

                    location = ""
                    for bf in p.get("bulletFields", []):
                        if bf and not bf.startswith("Posted"):
                            location = bf
                            break

                    all_jobs.append(JobPosting(
                        title=title,
                        url=url,
                        company=endpoint.company_name,
                        location=location,
                        date_posted=p.get("postedOn", "")[:10] if p.get("postedOn") else "",
                        source=f"workday-{endpoint.company_name.lower()}",
                    ))

                if offset + PAGE_SIZE >= total:
                    break
                offset += PAGE_SIZE
                await asyncio.sleep(0.5)

        return all_jobs

    async def _scan_smartrecruiters(self, endpoint: APIEndpoint, search_text: str) -> list[JobPosting]:
        """Scan SmartRecruiters API."""
        jobs = []

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
            resp = await client.get(
                f"https://api.smartrecruiters.com/v1/companies/{endpoint.company_name}/postings",
                params={"q": search_text, "limit": 50},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                },
            )

            if resp.status_code != 200:
                self.logger.warning(
                    f"{endpoint.company_name} (SmartRecruiters): HTTP {resp.status_code}"
                )
                return []

            data = resp.json()
            for item in data.get("content", []):
                loc_parts = []
                loc = item.get("location", {})
                if loc.get("city"):
                    loc_parts.append(loc["city"])
                if loc.get("country"):
                    loc_parts.append(loc["country"])

                jobs.append(JobPosting(
                    title=item.get("name", ""),
                    url=item.get("ref", ""),
                    company=endpoint.company_name,
                    location=", ".join(loc_parts),
                    date_posted=item.get("releasedDate", "")[:10] if item.get("releasedDate") else "",
                    source=f"smartrecruiters-{endpoint.company_name.lower()}",
                ))

        return jobs

    def _get_search_terms(self, company_name: str) -> list[str]:
        """Get search terms for a company from config."""
        for c in self.config.get("tracked_companies", []):
            if c["name"] == company_name:
                return c.get("search_terms", ["working student"])
        return ["working student"]
