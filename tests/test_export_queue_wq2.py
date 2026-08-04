"""WQ-2 unit tests for the deterministic, atomic queue exporter.

Covers:
- Every structured skip reason (evaluation_failed, missing_url,
  invalid_score, below_threshold, not_recommended, duplicate_url,
  missing_required_fields, missing_documents, document_not_found,
  duplicate_application_id)
- Deterministic, byte-stable output (no timestamps) and deterministic
  row ordering
- Duplicate handling for URLs AND application_ids
- Atomic publish: a failed write leaves the previous queue intact and
  leaves no temp files behind; a successful write replaces the queue
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.queue_exporter import (
    build_queue_entries,
    export_queue,
    BELOW_THRESHOLD,
    DOCUMENT_NOT_FOUND,
    DUPLICATE_APPLICATION_ID,
    DUPLICATE_URL,
    EVALUATION_FAILED,
    INVALID_SCORE,
    MISSING_DOCUMENTS,
    MISSING_REQUIRED_FIELDS,
    MISSING_URL,
    NOT_RECOMMENDED,
)

PROFILE = {"candidate": {"full_name": "Test User", "location": "Erlangen, Germany"}}
GREENHOUSE = "https://boards.greenhouse.io/example/jobs/1001"


def make_pdfs(tmp_path: Path) -> tuple[str, str]:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    cover.write_bytes(b"%PDF-1.4 fake")
    return str(cv), str(cover)


def make_eval(tmp_path: Path, **overrides) -> dict:
    cv, cover = make_pdfs(tmp_path)
    base = {
        "success": True,
        "url": GREENHOUSE,
        "company": "Acme",
        "title": "Engineer",
        "global_score": 4.5,
        "recommendation": "apply",
        "cv_pdf_path": cv,
        "cover_letter_pdf_path": cover,
        "description": "Do the things.",
    }
    base.update(overrides)
    return base


def reasons_of(skipped) -> list[str]:
    return [s["reason"] for s in skipped]


# ---------------------------------------------------------------------------
# Structured skip reasons
# ---------------------------------------------------------------------------


class TestSkipReasons:
    def test_evaluation_failed(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, success=False, error="model boom")], {}, {}, threshold=3.5
        )
        assert rows == []
        assert reasons_of(skipped) == [EVALUATION_FAILED]
        assert skipped[0]["detail"] == "model boom"

    def test_missing_url(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries([make_eval(tmp_path, url=None)], {}, {}, threshold=3.5)
        assert rows == []
        assert reasons_of(skipped) == [MISSING_URL]

    def test_invalid_score(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, global_score="not-a-number")], {}, {}, threshold=3.5
        )
        assert rows == []
        assert reasons_of(skipped) == [INVALID_SCORE]

    def test_below_threshold(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, global_score=2.9)], {}, {}, threshold=3.5
        )
        assert rows == []
        assert reasons_of(skipped) == [BELOW_THRESHOLD]
        assert "2.9" in skipped[0]["detail"]

    def test_not_recommended(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, recommendation="skip_german")], {}, {}, threshold=3.5
        )
        assert rows == []
        assert reasons_of(skipped) == [NOT_RECOMMENDED]

    def test_duplicate_url(self, tmp_path: Path) -> None:
        evals = [make_eval(tmp_path), make_eval(tmp_path, company="Other Corp")]
        rows, skipped = build_queue_entries(evals, {}, {}, threshold=3.5)
        assert len(rows) == 1
        assert reasons_of(skipped) == [DUPLICATE_URL]

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, company="", title="")], {}, {}, threshold=3.5
        )
        assert rows == []
        assert reasons_of(skipped) == [MISSING_REQUIRED_FIELDS]
        assert "company" in skipped[0]["detail"] and "title" in skipped[0]["detail"]

    def test_missing_documents(self, tmp_path: Path) -> None:
        rows, skipped = build_queue_entries(
            [make_eval(tmp_path, cv_pdf_path=None, cover_letter_pdf_path=None)],
            {},
            {},
            threshold=3.5,
        )
        assert rows == []
        assert reasons_of(skipped) == [MISSING_DOCUMENTS]

    def test_document_not_found(self, tmp_path: Path) -> None:
        missing_path = str(tmp_path / "does-not-exist.pdf")
        rows, skipped = build_queue_entries(
            [
                make_eval(
                    tmp_path,
                    cv_pdf_path=missing_path,
                    cover_letter_pdf_path=missing_path,
                )
            ],
            {},
            {},
            threshold=3.5,
        )
        assert rows == []
        assert reasons_of(skipped) == [DOCUMENT_NOT_FOUND]

    def test_duplicate_application_id(self, tmp_path: Path) -> None:
        # Distinct URLs, same platform + external id -> identical application_id.
        cv, cover = make_pdfs(tmp_path)
        evals = [
            {
                "success": True,
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "company": "Acme",
                "title": "Engineer",
                "global_score": 4.5,
                "recommendation": "apply",
                "cv_pdf_path": cv,
                "cover_letter_pdf_path": cover,
                "external_job_id": "shared-id",
            },
            {
                "success": True,
                "url": "https://boards.greenhouse.io/beta/jobs/2",
                "company": "Beta",
                "title": "PM",
                "global_score": 4.8,
                "recommendation": "apply",
                "cv_pdf_path": cv,
                "cover_letter_pdf_path": cover,
                "external_job_id": "shared-id",
            },
        ]
        rows, skipped = build_queue_entries(evals, {}, {}, threshold=3.5)
        assert len(rows) == 1
        assert reasons_of(skipped) == [DUPLICATE_APPLICATION_ID]

    def test_summary_counts_match(self, tmp_path: Path) -> None:
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(
            json.dumps(
                [
                    make_eval(tmp_path, global_score=2.0),
                    make_eval(tmp_path, recommendation="skip"),
                    make_eval(tmp_path, url="https://boards.greenhouse.io/other/jobs/9"),
                ]
            ),
            encoding="utf-8",
        )
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        summary = export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )
        # 3 evaluations: 1 exported (other/jobs/9), first two skipped.
        assert summary["exported"] == 1
        assert summary["skipped"] == 2
        assert summary["total_evaluations"] == 3
        assert summary["skipped_reasons"] == {
            BELOW_THRESHOLD: 1,
            NOT_RECOMMENDED: 1,
        }
        assert len(summary["skipped_jobs"]) == 2


# ---------------------------------------------------------------------------
# Determinism / byte stability
# ---------------------------------------------------------------------------


class TestDeterminism:
    def _run_export(
        self, tmp_path: Path, evals: list[dict], output_name: str = "queue.jsonl"
    ) -> bytes:
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps(evals), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
        output_path = tmp_path / output_name
        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )
        return output_path.read_bytes()

    def test_export_is_byte_stable(self, tmp_path: Path) -> None:
        evals = [make_eval(tmp_path, url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(5)]
        first = self._run_export(tmp_path, evals, "a.jsonl")
        second = self._run_export(tmp_path, evals, "b.jsonl")
        assert first == second  # identical bytes, no timestamps, deterministic order

    def test_output_order_independent_of_input_order(self, tmp_path: Path) -> None:
        evals = [make_eval(tmp_path, url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(5)]
        a = self._run_export(tmp_path, evals)
        b = self._run_export(tmp_path, list(reversed(evals)))
        assert a == b

    def test_rows_sorted_by_application_id(self, tmp_path: Path) -> None:
        evals = [make_eval(tmp_path, url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(3)]
        rows, _ = build_queue_entries(list(reversed(evals)), {}, {}, threshold=3.5)
        ids = [r["application_id"] for r in rows]
        assert ids == sorted(ids)

    def test_no_timestamps_in_rows(self, tmp_path: Path) -> None:
        rows, _ = build_queue_entries([make_eval(tmp_path)], {}, {}, threshold=3.5)
        for key in ("exported_at", "appended_at", "created_at", "timestamp"):
            assert key not in rows[0]
            assert key not in rows[0]["metadata"]


# ---------------------------------------------------------------------------
# Atomic publish
# ---------------------------------------------------------------------------


class TestAtomicPublish:
    def _prepare(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        cv, cover = make_pdfs(tmp_path)
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(
            json.dumps(
                [
                    {
                        "success": True,
                        "url": GREENHOUSE,
                        "company": "Acme",
                        "title": "Engineer",
                        "global_score": 4.5,
                        "recommendation": "apply",
                        "cv_pdf_path": cv,
                        "cover_letter_pdf_path": cover,
                    }
                ]
            ),
            encoding="utf-8",
        )
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"
        output_path.write_text("previous-completed-queue\n", encoding="utf-8")
        return evals_path, profile_path, output_path, tmp_path / "nope.md"

    def test_failure_keeps_previous_queue_and_no_temp(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        evals_path, profile_path, output_path, pipeline_path = self._prepare(tmp_path)

        def boom(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr("utils.queue_exporter.os.replace", boom)

        with pytest.raises(OSError):
            export_queue(
                output_path=output_path,
                evaluations_path=evals_path,
                pipeline_path=pipeline_path,
                profile_path=profile_path,
                threshold=3.5,
            )

        # The previous completed queue is untouched.
        assert output_path.read_text(encoding="utf-8") == "previous-completed-queue\n"
        # No stray temp files.
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_success_replaces_previous_queue(self, tmp_path: Path) -> None:
        evals_path, profile_path, output_path, pipeline_path = self._prepare(tmp_path)
        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=pipeline_path,
            profile_path=profile_path,
            threshold=3.5,
        )
        content = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(content) == 1  # one valid row; old content fully replaced
        assert json.loads(content[0])["status"] == "ready_to_apply"
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []