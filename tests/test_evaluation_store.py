"""Tests for the evaluation JSON store (utils/evaluation_store.py).

Proves the real persistence path:
    run_evaluate.py -> append_evaluation() -> data/evaluations.json
    run_export_queue.py -> load_evaluations() -> reads the same file

This is the canonical input for the UAA queue exporter. The test
simulates what run_evaluate.py does (calls append_evaluation with the
evaluation dict) and what run_export_queue.py does (calls
load_evaluations to read it back), then verifies the data round-trips
correctly.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.evaluation_store import append_evaluation, load_evaluations


class TestAppendEvaluation:
    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        assert not path.exists()
        append_evaluation({"url": "https://example.com/1", "score": 4.5}, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["url"] == "https://example.com/1"
        assert data[0]["score"] == 4.5

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        append_evaluation({"url": "https://example.com/1"}, path)
        append_evaluation({"url": "https://example.com/2"}, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_deduplicates_by_url(self, tmp_path: Path) -> None:
        """Re-evaluating the same job updates its record, not appends a dup."""
        path = tmp_path / "evaluations.json"
        append_evaluation({"url": "https://example.com/1", "score": 3.0}, path)
        append_evaluation({"url": "https://example.com/1", "score": 4.5}, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["score"] == 4.5  # updated, not appended

    def test_adds_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        append_evaluation({"url": "https://example.com/1"}, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "appended_at" in data[0]

    def test_handles_non_serializable_values(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        # datetime is not JSON-serializable by default; the store must
        # convert it to ISO format.
        from datetime import datetime

        append_evaluation(
            {"url": "https://example.com/1", "evaluated_at": datetime(2026, 7, 14)},
            path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "2026-07-14" in data[0]["evaluated_at"]

    def test_resets_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        append_evaluation({"url": "https://example.com/1"}, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_atomic_write(self, tmp_path: Path) -> None:
        """No .tmp file left after a successful write."""
        path = tmp_path / "evaluations.json"
        append_evaluation({"url": "https://example.com/1"}, path)
        assert not (tmp_path / "evaluations.json.tmp").exists()


class TestLoadEvaluations:
    def test_returns_empty_if_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        assert load_evaluations(path) == []

    def test_loads_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        append_evaluation({"url": "https://example.com/1", "score": 4.5}, path)
        records = load_evaluations(path)
        assert len(records) == 1
        assert records[0]["url"] == "https://example.com/1"
        assert records[0]["score"] == 4.5

    def test_returns_empty_for_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        path.write_text("not json", encoding="utf-8")
        assert load_evaluations(path) == []

    def test_returns_empty_for_non_array_json(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.json"
        path.write_text('{"not": "an array"}', encoding="utf-8")
        assert load_evaluations(path) == []


class TestRealPersistenceRoundTrip:
    """Proves the real chain: append_evaluation (what run_evaluate.py
    calls) -> load_evaluations (what run_export_queue.py calls) -> queue
    row built by build_queue_rows.
    """

    def test_run_evaluate_to_exporter_round_trip(self, tmp_path: Path) -> None:
        """Simulate run_evaluate.py persisting an evaluation, then
        run_export_queue.py reading it and building a queue row."""
        # Create fake PDF artifacts (as run_evaluate would).
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake cv")
        cover.write_bytes(b"%PDF-1.4 fake cover")

        # 1. run_evaluate.py calls append_evaluation with the evaluation dict.
        evals_path = tmp_path / "evaluations.json"
        evaluation = {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/rt-001",
            "company": "RoundTrip Corp",
            "title": "Software Engineer",
            "global_score": 4.5,
            "recommendation": "apply",
            "cv_pdf_path": str(cv),
            "cover_letter_pdf_path": str(cover),
            "description": "Build web apps.",
            "date_posted": "2026-07-10",
            "german_level_required": "none",
        }
        append_evaluation(evaluation, evals_path)

        # 2. run_export_queue.py calls load_evaluations to read it back.
        from utils.queue_exporter import build_queue_rows, extract_candidate_profile_snapshot

        loaded = load_evaluations(evals_path)
        assert len(loaded) == 1
        assert loaded[0]["url"] == evaluation["url"]

        # 3. build_queue_rows produces a UAA-compatible queue row.
        profile_snapshot = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "email": "test@example.com"}}
        )
        rows = build_queue_rows(loaded, {}, profile_snapshot, threshold=3.5)
        assert len(rows) == 1
        row = rows[0]
        assert row["url"] == evaluation["url"]
        assert row["company"] == "RoundTrip Corp"
        assert row["cv_pdf"] == str(cv.resolve())
        assert row["cover_letter_pdf"] == str(cover.resolve())
        assert row["verdict"] == "apply"
        assert row["status"] == "ready_to_apply"
        assert row["metadata"]["candidate_profile"]["email"] == "test@example.com"
