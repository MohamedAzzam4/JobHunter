"""
Deduplication logic for job postings.

Tracks seen jobs via:
1. URL exact match (scan-history.tsv)
2. Company + title normalized match (applications.md)
3. In-memory set for intra-scan dedup

Thread-safe for concurrent scanner use.
"""

import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class DeduplicatorStore:
    """Manages seen URLs and company+role pairs for deduplication.
    
    Loads from scan-history.tsv, pipeline.md, and applications.md.
    Thread-safe via a lock for concurrent scanner use.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._seen_urls: set[str] = set()
        self._seen_company_roles: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """Load all dedup sources from disk."""
        self._load_scan_history()
        self._load_pipeline()
        self._load_applications()
        logger.info(
            f"Dedup loaded: {len(self._seen_urls)} URLs, "
            f"{len(self._seen_company_roles)} company+role pairs"
        )

    def _load_scan_history(self):
        """Load URLs from scan-history.tsv."""
        path = self.data_dir / "scan-history.tsv"
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:  # Skip header
                    continue
                parts = line.strip().split("\t")
                if parts and parts[0]:
                    self._seen_urls.add(parts[0])

    def _load_pipeline(self):
        """Load URLs from pipeline.md checkbox lines."""
        path = self.data_dir / "pipeline.md"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"- \[[ x/]\] (https?://\S+)", text):
            self._seen_urls.add(match.group(1))

    def _load_applications(self):
        """Load company+role pairs from applications.md table."""
        path = self.data_dir / "applications.md"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        # Parse markdown table rows: | # | Date | Company | Role | ...
        for match in re.finditer(r"\|[^|]+\|[^|]+\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|", text):
            company = match.group(1).strip().lower()
            role = match.group(2).strip().lower()
            if company and role and company != "company":
                self._seen_company_roles.add(f"{company}::{role}")

        # Also extract any inline URLs
        for match in re.finditer(r"https?://[^\s|)]+", text):
            self._seen_urls.add(match.group(0))

    def is_duplicate(self, job: dict) -> tuple[bool, str]:
        """Check if a job is a duplicate.
        
        Args:
            job: Dict with 'url', 'title', 'company' keys
            
        Returns:
            Tuple of (is_dup: bool, reason: str)
        """
        url = job.get("url", "")
        company = job.get("company", "").lower()
        title = job.get("title", "").lower()

        with self._lock:
            # Check URL
            if url and url in self._seen_urls:
                return True, "url_seen"

            # Check company + role
            key = f"{company}::{title}"
            if company and title and key in self._seen_company_roles:
                return True, "company_role_seen"

            return False, ""

    def mark_seen(self, job: dict):
        """Mark a job as seen (for intra-scan dedup)."""
        with self._lock:
            url = job.get("url", "")
            if url:
                self._seen_urls.add(url)

            company = job.get("company", "").lower()
            title = job.get("title", "").lower()
            if company and title:
                self._seen_company_roles.add(f"{company}::{title}")

    @property
    def stats(self) -> dict:
        """Return current dedup stats."""
        return {
            "urls_tracked": len(self._seen_urls),
            "company_roles_tracked": len(self._seen_company_roles),
        }
