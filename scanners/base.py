"""
Abstract base scanner.

All scanners inherit from this and implement the `scan()` method.
Provides shared logging, config loading, and result normalization.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


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
        }


@dataclass
class ScanResult:
    """Result from a single scanner run."""
    scanner_name: str
    jobs: list[JobPosting] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    
    @property
    def job_count(self) -> int:
        return len(self.jobs)
    
    @property
    def error_count(self) -> int:
        return len(self.errors)


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
        """Mark a ScanResult as finished."""
        result.finished_at = datetime.now().isoformat()
        self.logger.info(
            f"Scan complete: {result.job_count} jobs, {result.error_count} errors"
        )
        return result
