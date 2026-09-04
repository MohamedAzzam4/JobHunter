"""
Abstract base scanner.

All scanners inherit from this and implement the `scan()` method.
Provides shared logging, config loading, and result normalization.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ScanStatus(str, Enum):
    """Machine-readable scanner outcome.

    A 200 response with zero jobs is SUCCESS_EMPTY (a valid empty
    payload). HTTP 404/422 are REQUEST_CONTRACT_FAILURE — a broken
    request contract, never "zero jobs".
    """

    SUCCESS_WITH_RESULTS = "SUCCESS_WITH_RESULTS"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    REQUEST_CONTRACT_FAILURE = "REQUEST_CONTRACT_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH_FAILURE = "AUTH_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    UNSUPPORTED_SCANNER = "UNSUPPORTED_SCANNER"


def classify_http_status(status_code: int) -> str | None:
    """Map an HTTP status to a failure category, or None when OK.

    200 → None (caller decides SUCCESS_WITH_RESULTS vs SUCCESS_EMPTY).
    404/422 → REQUEST_CONTRACT_FAILURE. 401/403 → AUTH_FAILURE.
    429 → RATE_LIMIT. 5xx → NETWORK_FAILURE (server side).
    """
    if status_code == 200:
        return None
    if status_code in (404, 422, 400, 405, 410):
        return ScanStatus.REQUEST_CONTRACT_FAILURE.value
    if status_code in (401, 403):
        return ScanStatus.AUTH_FAILURE.value
    if status_code == 429:
        return ScanStatus.RATE_LIMIT.value
    return ScanStatus.NETWORK_FAILURE.value


def classify_exception(exc: BaseException) -> str:
    """Map an exception to a failure category without leaking details."""
    name = type(exc).__name__
    message = str(exc).lower()
    # Transparent wrappers embed the inner category as "[CATEGORY]" (e.g.
    # websearch _scan_site wraps the classified HTTP status). Honor it so
    # the recorded category matches the underlying failure.
    for status in ScanStatus:
        if f"[{status.value}]" in str(exc):
            return status.value
    if isinstance(exc, ImportError) or "not installed" in message:
        return ScanStatus.DEPENDENCY_FAILURE.value
    if "rate limit" in message or "429" in message:
        return ScanStatus.RATE_LIMIT.value
    if "timeout" in name or "connect" in name or "network" in message:
        return ScanStatus.NETWORK_FAILURE.value
    if "numpy" in message or "longdouble" in message:
        return ScanStatus.DEPENDENCY_FAILURE.value
    return ScanStatus.NETWORK_FAILURE.value


@dataclass
class JobPosting:
    """Normalized job posting from any scanner source."""
    title: str
    url: str
    company: str
    location: str = ""
    date_posted: str = ""
    source: str = ""          # e.g. "workday", "jobspy-indeed", "jobspy-linkedin"
    description: str = ""     # Full JD text if available from scanner
    salary: str = ""
    target_quality: str = ""  # DIRECT_ATS / CAREER_DETAIL_SAFE_APPLY / SOURCE_ONLY / UNKNOWN

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "company": self.company,
            "location": self.location,
            "date_posted": self.date_posted,
            "source": self.source,
            "description": self.description,
            "salary": self.salary,
            "target_quality": self.target_quality,
        }


@dataclass
class ScanResult:
    """Result from a single scanner run."""
    scanner_name: str
    jobs: list[JobPosting] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    status: str = ScanStatus.SUCCESS_WITH_RESULTS.value
    error_category: str | None = None

    @property
    def job_count(self) -> int:
        return len(self.jobs)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def note_error(self, message: str, category: str | None = None) -> None:
        """Record an error with an optional failure category.

        The first non-empty category wins; ``finalize()`` promotes it to
        ``status`` when no jobs were found.
        """
        self.errors.append(message)
        if category and not self.error_category:
            self.error_category = category

    def finalize(self) -> "ScanResult":
        """Derive status from jobs/errors. Call before returning from scan()."""
        if self.jobs:
            self.status = ScanStatus.SUCCESS_WITH_RESULTS.value
            self.error_category = None
        elif self.error_category:
            self.status = self.error_category
        elif self.errors:
            self.status = ScanStatus.NETWORK_FAILURE.value
            self.error_category = ScanStatus.NETWORK_FAILURE.value
        else:
            self.status = ScanStatus.SUCCESS_EMPTY.value
            self.error_category = None
        self.finished_at = datetime.now().isoformat()
        return self


class BaseScanner(ABC):
    """Abstract base class for all job scanners."""

    def __init__(self, config: dict, name: str = "base"):
        self.config = config
        self.name = name
        self.logger = logging.getLogger(f"scanner.{name}")

    @abstractmethod
    async def scan(self) -> ScanResult:
        """Execute the scan and return results.
        
        Must be implemented by subclasses. Should:
        1. Fetch job listings from the source
        2. Normalize into JobPosting objects
        3. Handle errors gracefully (log and continue)
        4. Return a ScanResult
        """
        pass

    def _make_result(self) -> ScanResult:
        """Create a new ScanResult with timestamp."""
        return ScanResult(
            scanner_name=self.name,
            started_at=datetime.now().isoformat(),
        )

    def _finish_result(self, result: ScanResult) -> ScanResult:
        """Derive status, mark finished, and log."""
        result.finalize()
        self.logger.info(
            f"Scan complete: {result.job_count} jobs, "
            f"{result.error_count} errors, status={result.status}"
        )
        return result
