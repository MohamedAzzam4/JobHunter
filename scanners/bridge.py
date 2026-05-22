"""
Bridge module — merges all scanner outputs into a unified pipeline.

Responsibilities:
1. Collect results from all scanners
2. Apply title + location filters
3. Deduplicate against history
4. Write new jobs to pipeline.md
5. Write scan history to scan-history.tsv
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from .base import ScanResult, JobPosting
from utils.filters import TitleFilter, LocationFilter, filter_jobs
from utils.dedup import DeduplicatorStore
from utils.jd_cache import JDCache

logger = logging.getLogger(__name__)


class PipelineBridge:
    """Merges scanner results into pipeline.md with dedup and filtering."""

    def __init__(self, config: dict, data_dir: str = "data"):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.title_filter = TitleFilter(config)
        self.location_filter = LocationFilter(config)
        self.dedup = DeduplicatorStore(data_dir)
        self.jd_cache = JDCache()

        # Ensure pipeline.md exists
        self.pipeline_path = self.data_dir / "pipeline.md"
        if not self.pipeline_path.exists():
            self._create_pipeline_file()

        # Ensure scan-history.tsv exists
        self.history_path = self.data_dir / "scan-history.tsv"
        if not self.history_path.exists():
            self._create_history_file()

    def _create_pipeline_file(self):
        """Create initial pipeline.md."""
        self.pipeline_path.write_text(
            "# Job Pipeline\n\n## Pending\n\n## Processed\n",
            encoding="utf-8",
        )

    def _create_history_file(self):
        """Create initial scan-history.tsv with header."""
        self.history_path.write_text(
            "url\tfirst_seen\tsource\ttitle\tcompany\tstatus\tlocation\n",
            encoding="utf-8",
        )

    def process(self, scan_results: list[ScanResult], dry_run: bool = False) -> dict:
        """Process all scanner results through filter → dedup → pipeline.
        
        Args:
            scan_results: List of ScanResult from all scanners
            dry_run: If True, don't write to disk
            
        Returns:
            Summary dict with counts
        """
        date = datetime.now().strftime("%Y-%m-%d")

        # 1. Collect all jobs
        all_jobs = []
        for result in scan_results:
            for job in result.jobs:
                all_jobs.append(job.to_dict())

        logger.info(f"Total jobs from all scanners: {len(all_jobs)}")

        # 2. Apply filters
        filtered = filter_jobs(all_jobs, self.title_filter, self.location_filter)

        passed = filtered["passed"]
        rejected_title = filtered["rejected_title"]
        rejected_location = filtered["rejected_location"]

        # 3. Deduplicate
        new_jobs = []
        duplicates = []
        for job in passed:
            is_dup, reason = self.dedup.is_duplicate(job)
            if is_dup:
                duplicates.append({**job, "dedup_reason": reason})
            else:
                new_jobs.append(job)
                self.dedup.mark_seen(job)

        logger.info(
            f"After dedup: {len(new_jobs)} new, {len(duplicates)} duplicates"
        )

        # 4. Write to pipeline.md and scan-history.tsv
        if not dry_run and new_jobs:
            self._append_to_pipeline(new_jobs)
            self._append_to_history(new_jobs, date, "added")
            # Cache JDs from scanner (critical for Indeed which blocks re-fetch)
            for job in new_jobs:
                desc = job.get("description", "")
                if desc:
                    self.jd_cache.put(
                        job.get("url", ""),
                        desc,
                        title=job.get("title", ""),
                        company=job.get("company", ""),
                        date_posted=str(job.get("date_posted", "")),
                    )
            self.jd_cache.save()

        # Write rejected/duped to history too (for tracking)
        if not dry_run:
            self._append_to_history(rejected_title, date, "skipped_title")
            self._append_to_history(rejected_location, date, "skipped_location")
            self._append_to_history(duplicates, date, "skipped_dup")

        # 5. Build summary
        summary = {
            "total_found": len(all_jobs),
            "passed_filters": len(passed),
            "rejected_title": len(rejected_title),
            "rejected_location": len(rejected_location),
            "duplicates": len(duplicates),
            "new_added": len(new_jobs),
            "new_jobs": new_jobs,
            "errors": [],
        }

        # Collect scanner errors
        for result in scan_results:
            for err in result.errors:
                summary["errors"].append(f"[{result.scanner_name}] {err}")

        return summary

    def _append_to_pipeline(self, jobs: list[dict]):
        """Append new jobs to pipeline.md under '## Pending'."""
        text = self.pipeline_path.read_text(encoding="utf-8")

        lines = []
        for job in jobs:
            url = job.get("url", "")
            company = job.get("company", "Unknown").replace("|", "-")
            title = job.get("title", "Unknown").replace("|", "-")
            location = job.get("location", "")
            loc_suffix = f" [{location}]" if location else ""
            lines.append(f"- [ ] {url} | {company} | {title}{loc_suffix}")

        block = "\n".join(lines) + "\n"

        # Support both new (Pending) and legacy (Pendientes) headers
        marker = "## Pending"
        idx = text.find(marker)
        if idx == -1:
            marker = "## Pendientes"  # Legacy fallback
            idx = text.find(marker)
        if idx != -1:
            insert_at = idx + len(marker)
            # Skip any existing newlines right after the marker
            while insert_at < len(text) and text[insert_at] == "\n":
                insert_at += 1
            text = text[:insert_at] + "\n" + block + text[insert_at:]
        else:
            # No Pending section — prepend
            text = f"## Pending\n\n{block}\n{text}"

        self.pipeline_path.write_text(text, encoding="utf-8")
        logger.info(f"Added {len(jobs)} jobs to pipeline.md")

    def _append_to_history(self, jobs: list[dict], date: str, status: str):
        """Append jobs to scan-history.tsv."""
        if not jobs:
            return

        lines = []
        for job in jobs:
            url = job.get("url", "")
            source = job.get("source", "unknown")
            title = job.get("title", "").replace("\t", " ")
            company = job.get("company", "").replace("\t", " ")
            location = job.get("location", "").replace("\t", " ")
            lines.append(
                f"{url}\t{date}\t{source}\t{title}\t{company}\t{status}\t{location}\n"
            )

        with open(self.history_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
