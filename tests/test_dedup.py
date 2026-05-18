"""
Tests for deduplication logic.
"""
import os
import sys
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.dedup import DeduplicatorStore


@pytest.fixture
def empty_dedup(tmp_path):
    """Create a dedup store with empty data dir."""
    return DeduplicatorStore(str(tmp_path))


@pytest.fixture
def populated_dedup(tmp_path):
    """Create a dedup store with some existing history."""
    # Create scan-history.tsv
    history = tmp_path / "scan-history.tsv"
    history.write_text(
        "url\tfirst_seen\tsource\ttitle\tcompany\tstatus\tlocation\n"
        "https://example.com/job1\t2026-05-17\tindeed\tWerkstudent AI\tSiemens\tadded\tErlangen\n"
        "https://example.com/job2\t2026-05-17\tlinkedin\tWorking Student\tAdidas\tadded\tHerzo\n",
        encoding="utf-8",
    )
    return DeduplicatorStore(str(tmp_path))


class TestDeduplicator:
    def test_empty_store_no_duplicates(self, empty_dedup):
        job = {"url": "https://example.com/new", "title": "Working Student", "company": "Siemens"}
        is_dup, reason = empty_dedup.is_duplicate(job)
        assert is_dup is False
        assert reason == ""

    def test_url_exact_match(self, populated_dedup):
        job = {"url": "https://example.com/job1", "title": "Any Title", "company": "Any"}
        is_dup, reason = populated_dedup.is_duplicate(job)
        assert is_dup is True
        assert reason == "url_seen"

    def test_url_not_matched(self, populated_dedup):
        job = {"url": "https://example.com/job999", "title": "New Role", "company": "New"}
        is_dup, reason = populated_dedup.is_duplicate(job)
        assert is_dup is False

    def test_mark_seen_then_duplicate(self, empty_dedup):
        job = {"url": "https://example.com/new", "title": "Working Student", "company": "Siemens"}
        empty_dedup.mark_seen(job)
        is_dup, reason = empty_dedup.is_duplicate(job)
        assert is_dup is True
        assert reason == "url_seen"

    def test_company_role_dedup(self, empty_dedup):
        job1 = {"url": "https://a.com/1", "title": "Working Student AI", "company": "Siemens"}
        empty_dedup.mark_seen(job1)

        # Same company + title, different URL
        job2 = {"url": "https://b.com/2", "title": "Working Student AI", "company": "Siemens"}
        is_dup, reason = empty_dedup.is_duplicate(job2)
        assert is_dup is True
        assert reason == "company_role_seen"

    def test_different_company_same_title(self, empty_dedup):
        job1 = {"url": "https://a.com/1", "title": "Working Student AI", "company": "Siemens"}
        empty_dedup.mark_seen(job1)

        job2 = {"url": "https://b.com/2", "title": "Working Student AI", "company": "Bosch"}
        is_dup, reason = empty_dedup.is_duplicate(job2)
        assert is_dup is False

    def test_empty_url_no_crash(self, empty_dedup):
        job = {"url": "", "title": "", "company": ""}
        is_dup, reason = empty_dedup.is_duplicate(job)
        assert is_dup is False

    def test_stats(self, populated_dedup):
        stats = populated_dedup.stats
        assert stats["urls_tracked"] == 2
        assert stats["company_roles_tracked"] == 0  # Only TSV URLs are loaded, not role pairs

    def test_missing_files(self, tmp_path):
        """Store should work even if no history files exist."""
        store = DeduplicatorStore(str(tmp_path / "nonexistent"))
        assert store.stats["urls_tracked"] == 0
