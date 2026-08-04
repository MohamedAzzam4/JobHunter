"""Integration tests for the WQ-2 queue-export step inside run_all.py.

Verified using mocks only (no network, LLM, browser, or credentials):

- After a successful scan + evaluate run, the queue is exported exactly once.
- The export failure propagates (a failed publish fails the pipeline).
- No export happens after an upstream failure (evaluation raises), and no
  export happens in dry-run, scan-only, or no-new-jobs short-circuit paths.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import run_all  # noqa: E402


SCAN_SUMMARY = {"total_found": 3, "new_added": 2}
EVAL_RESULTS = [
    {"success": True, "global_score": 4.5, "company": "Acme", "title": "Engineer"},
    {"success": True, "global_score": 2.8, "company": "Low", "title": "Dev"},
]
EXPORT_SUMMARY = {
    "exported": 1,
    "skipped": 1,
    "skipped_reasons": {"below_threshold": 1},
    "output_path": "data/application_queue.jsonl",
}


async def fake_scan(dry_run: bool = False) -> dict:
    return SCAN_SUMMARY


async def fake_evaluate(mode: str = "all", threshold_override=None, german_policy=None) -> list:
    return EVAL_RESULTS


def install_fakes(monkeypatch, export_raise: Exception | None = None) -> list:
    calls: list = []

    def fake_export(threshold=None, **kwargs) -> dict:
        calls.append({"threshold": threshold, "kwargs": kwargs})
        if export_raise is not None:
            raise export_raise
        return EXPORT_SUMMARY

    monkeypatch.setattr(run_all, "run_scan", fake_scan)
    monkeypatch.setattr(run_all, "run_evaluate", fake_evaluate)
    monkeypatch.setattr(run_all, "export_queue", fake_export)
    return calls


class TestRunAllQueueExport:
    def test_exports_exactly_once_after_success(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch)
        asyncio.run(run_all.run_pipeline())
        assert len(calls) == 1
        assert calls[0]["threshold"] is not None  # resolved from profile/CLI

    def test_export_failure_propagates(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch, export_raise=RuntimeError("publish failed"))
        with pytest.raises(RuntimeError):
            asyncio.run(run_all.run_pipeline())
        assert len(calls) == 1  # export was attempted exactly once, then raised

    def test_no_export_when_evaluation_fails(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch)

        async def failing_evaluate(mode="all", threshold_override=None, german_policy=None):
            raise RuntimeError("evaluation stage crashed")

        monkeypatch.setattr(run_all, "run_evaluate", failing_evaluate)
        with pytest.raises(RuntimeError):
            asyncio.run(run_all.run_pipeline())
        assert calls == []

    def test_no_export_in_dry_run(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch)
        asyncio.run(run_all.run_pipeline(dry_run=True))
        assert calls == []

    def test_no_export_in_scan_only(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch)
        asyncio.run(run_all.run_pipeline(scan_only=True))
        assert calls == []

    def test_no_export_when_no_new_jobs(self, monkeypatch) -> None:
        calls = install_fakes(monkeypatch)

        async def empty_scan(dry_run: bool = False) -> dict:
            return {"total_found": 5, "new_added": 0}

        monkeypatch.setattr(run_all, "run_scan", empty_scan)
        asyncio.run(run_all.run_pipeline())
        assert calls == []