"""
Job description cache.

Stores JD text fetched during scanning so we don't need to re-fetch
during evaluation (especially important for Indeed which blocks re-requests).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class JDCache:
    """Simple JSON-file cache for job descriptions keyed by URL."""

    def __init__(self, cache_path: str = "data/jd_cache.json"):
        self.path = Path(cache_path)
        self._cache = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.warning("Corrupted JD cache, starting fresh")
                return {}
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=1)

    def get(self, url: str) -> str | None:
        """Get cached JD text for a URL, or None."""
        return self._cache.get(url)

    def put(self, url: str, description: str, title: str = "", company: str = ""):
        """Cache a JD. Only stores if description is non-empty."""
        if not description or not url:
            return
        self._cache[url] = {
            "description": description[:15000],
            "title": title,
            "company": company,
        }

    def save(self):
        """Persist to disk. Call after a batch of puts."""
        self._save()
        logger.info("JD cache saved: %d entries", len(self._cache))

    @property
    def size(self) -> int:
        return len(self._cache)
