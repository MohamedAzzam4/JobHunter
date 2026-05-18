"""
Tests for the pipeline bridge module.
"""
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scanners.base import ScanResult, JobPosting
from scanners.bridge import PipelineBridge


@pytest.fixture
def bridge_config():
    return {
        "title_filter": {
            "positive": ["Working Student", "Werkstudent"],
            "negative": ["Senior"],
        },
        "location_filter": {
            "allow": ["Erlangen", "Munich", "Germany"],
            "block": [],
        },
    }


@pytest.fixture
def bridge(bridge_config, tmp_path):
    return PipelineBridge(bridge_config, str(tmp_path))


@pytest.fixture
def sample_scan_result():
    return ScanResult(
        scanner_name="test",
        jobs=[
            JobPosting(
                title="Working Student AI",
                url="https://example.com/job1",
                company="Siemens",
                location="Erlangen, Germany",
                source="test",
            ),
            JobPosting(
                title="Working Student HR",
                url="https://example.com/job2",
                company="Adidas",
                location="Munich, Germany",
                source="test",
            ),
            JobPosting(
                title="Senior Engineer",
                url="https://example.com/job3",
                company="Bosch",
                location="Erlangen",
                source="test",
            ),
            JobPosting(
                title="Working Student Design",
                url="https://example.com/job4",
                company="Puma",
                location="Berlin",
                source="test",
            ),
        ],
    )


class TestPipelineBridge:
    def test_process_filters_and_dedup(self, bridge, sample_scan_result, tmp_path):
        summary = bridge.process([sample_scan_result])

        assert summary["total_found"] == 4
        assert summary["new_added"] == 2  # AI + HR pass, Senior rejected, Berlin rejected
        assert summary["rejected_title"] == 1
        assert summary["rejected_location"] == 1

        # Check pipeline.md was written
        pipeline_text = (tmp_path / "pipeline.md").read_text(encoding="utf-8")
        assert "Working Student AI" in pipeline_text
        assert "Working Student HR" in pipeline_text
        assert "Senior Engineer" not in pipeline_text

    def test_dry_run_no_write(self, bridge, sample_scan_result, tmp_path):
        summary = bridge.process([sample_scan_result], dry_run=True)

        assert summary["new_added"] == 2

        # Pipeline should only have the initial template
        pipeline_text = (tmp_path / "pipeline.md").read_text(encoding="utf-8")
        assert "Working Student AI" not in pipeline_text

    def test_dedup_on_second_run(self, bridge, sample_scan_result, tmp_path):
        # First run
        summary1 = bridge.process([sample_scan_result])
        assert summary1["new_added"] == 2

        # Second run with same data — should be all duplicates
        bridge2 = PipelineBridge(bridge.config, str(tmp_path))
        summary2 = bridge2.process([sample_scan_result])
        assert summary2["new_added"] == 0
        assert summary2["duplicates"] == 2

    def test_scan_history_written(self, bridge, sample_scan_result, tmp_path):
        bridge.process([sample_scan_result])

        history = (tmp_path / "scan-history.tsv").read_text(encoding="utf-8")
        lines = history.strip().split("\n")
        # Header + 2 added + 1 skipped_title + 1 skipped_location = 5
        assert len(lines) == 5

    def test_empty_scan_results(self, bridge):
        summary = bridge.process([])
        assert summary["total_found"] == 0
        assert summary["new_added"] == 0

    def test_scanner_errors_collected(self, bridge):
        result = ScanResult(
            scanner_name="broken",
            jobs=[],
            errors=["Connection timeout", "Rate limited"],
        )
        summary = bridge.process([result])
        assert len(summary["errors"]) == 2
        assert "broken" in summary["errors"][0]
