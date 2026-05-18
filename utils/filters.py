"""
Title and location filters for job postings.

Reads filter config from portals.yml and provides reusable filter functions.
All matching is case-insensitive substring matching.
"""

import logging

logger = logging.getLogger(__name__)


class TitleFilter:
    """Filters job titles based on positive/negative keyword lists."""

    def __init__(self, config: dict):
        raw = config.get("title_filter", {})
        self.positive = [k.lower() for k in raw.get("positive", [])]
        self.negative = [k.lower() for k in raw.get("negative", [])]

    def matches(self, title: str) -> bool:
        """Return True if title passes the filter.
        
        Rules:
        - If positive list is empty, everything passes (no restriction)
        - At least one positive keyword must appear
        - Zero negative keywords must appear
        - Empty/None title always fails
        """
        if not title or not title.strip():
            return False

        lower = title.lower()

        # Check negatives first (faster rejection)
        for neg in self.negative:
            if neg in lower:
                logger.debug(f"Title rejected (negative '{neg}'): {title}")
                return False

        # Check positives
        if not self.positive:
            return True

        for pos in self.positive:
            if pos in lower:
                return True

        logger.debug(f"Title rejected (no positive match): {title}")
        return False


class LocationFilter:
    """Filters job locations based on allow/block keyword lists.
    
    Semantics:
    - If no location_filter config exists, everything passes
    - Empty/None location always passes (don't penalize missing data)
    - Block keywords take precedence over allow
    - If allow list is empty, everything passes (already cleared block)
    - If allow list is non-empty, at least one must match
    """

    def __init__(self, config: dict):
        raw = config.get("location_filter", {})
        self.allow = [k.lower() for k in raw.get("allow", [])]
        self.block = [k.lower() for k in raw.get("block", [])]
        self._enabled = bool(raw)  # disabled if section is absent

    def matches(self, location: str) -> bool:
        """Return True if location passes the filter."""
        if not self._enabled:
            return True

        if not location or not location.strip():
            return True  # Don't penalize missing location data

        lower = location.lower()

        # Block takes precedence
        for blk in self.block:
            if blk in lower:
                logger.debug(f"Location blocked ('{blk}'): {location}")
                return False

        # If allow is empty, everything passes
        if not self.allow:
            return True

        # At least one allow keyword must match
        for alw in self.allow:
            if alw in lower:
                return True

        logger.debug(f"Location rejected (no allow match): {location}")
        return False


def filter_jobs(jobs: list[dict], title_filter: TitleFilter, location_filter: LocationFilter) -> dict:
    """Filter a list of jobs, returning categorized results.

    Args:
        jobs: List of dicts with at least 'title' and 'location' keys
        title_filter: TitleFilter instance
        location_filter: LocationFilter instance

    Returns:
        Dict with 'passed', 'rejected_title', 'rejected_location' lists
    """
    passed = []
    rejected_title = []
    rejected_location = []

    for job in jobs:
        title = job.get("title", "")
        location = job.get("location", "")

        if not title_filter.matches(title):
            rejected_title.append(job)
        elif not location_filter.matches(location):
            rejected_location.append(job)
        else:
            passed.append(job)

    logger.info(
        f"Filtered {len(jobs)} jobs: {len(passed)} passed, "
        f"{len(rejected_title)} rejected by title, "
        f"{len(rejected_location)} rejected by location"
    )

    return {
        "passed": passed,
        "rejected_title": rejected_title,
        "rejected_location": rejected_location,
    }
