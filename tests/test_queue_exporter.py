"""Tests for the queue exporter (utils/queue_exporter.py).

Covers:
- Candidate profile extraction from profile.yml
- Platform detection from URL
- Application ID computation (must match UAA's identity algorithm)
- Building queue rows with filters (score threshold, verdict, documents)
- Idempotent export (same input -> same output)
- Missing CV/cover letter PDF -> job excluded
- Below-threshold job -> excluded
- Non-apply verdict -> excluded
- Candidate profile snapshot embedded in metadata
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
    build_queue_rows,
    export_queue,
    extract_candidate_profile_snapshot,
    load_profile,
    _compute_application_id,
    _detect_platform,
    _detect_source,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_profile() -> dict:
    return {
        "candidate": {
            "full_name": "Mohamed Abd Elrhman Azzam",
            "short_name": "Mohamed Azzam",
            "subtitle": "AI Engineer | Working-Student Candidate",
            "email": "mohammed.abd.elrhman.azzam@gmail.com",
            "phone": "+49 152 5617 2336",
            "location": "Erlangen, Germany",
            "linkedin": "https://www.linkedin.com/in/mohamed-azzam-61407a227/",
            "github": "https://github.com/MohamedAzzam4",
        },
        "evaluation": {
            "auto_cv_threshold": 3.5,
        },
    }


@pytest.fixture
def sample_profile_snapshot(sample_profile) -> dict:
    return extract_candidate_profile_snapshot(sample_profile)


@pytest.fixture
def sample_evaluations(tmp_path: Path) -> list[dict]:
    """A list of evaluations with varied scores/verdicts/documents."""
    # Create fake PDF files so the exporter's existence check passes.
    cv1 = tmp_path / "company1-cv.pdf"
    cv1.write_bytes(b"%PDF-1.4 fake cv")
    cover1 = tmp_path / "company1-cover.pdf"
    cover1.write_bytes(b"%PDF-1.4 fake cover")

    cv2 = tmp_path / "company2-cv.pdf"
    cv2.write_bytes(b"%PDF-1.4 fake cv 2")
    cover2 = tmp_path / "company2-cover.pdf"
    cover2.write_bytes(b"%PDF-1.4 fake cover 2")

    return [
        # 1. Valid apply job with PDFs (should be exported)
        {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1001",
            "company": "Example Corp",
            "title": "Software Engineer",
            "global_score": 4.5,
            "recommendation": "apply",
            "cv_pdf_path": str(cv1),
            "cover_letter_pdf_path": str(cover1),
            "description": "Build web apps.",
            "date_posted": "2026-07-10",
            "german_level_required": "none",
        },
        # 2. Below threshold (should be excluded)
        {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1002",
            "company": "Low Corp",
            "title": "Junior Dev",
            "global_score": 2.0,
            "recommendation": "apply",
            "cv_pdf_path": str(cv2),
            "cover_letter_pdf_path": str(cover2),
        },
        # 3. Verdict skip_german (should be excluded)
        {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1003",
            "company": "German Corp",
            "title": "Entwickler",
            "global_score": 4.8,
            "recommendation": "skip_german",
            "cv_pdf_path": str(cv1),
            "cover_letter_pdf_path": str(cover1),
        },
        # 4. Missing CV PDF (should be excluded)
        {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1004",
            "company": "NoPDF Corp",
            "title": "Data Scientist",
            "global_score": 4.0,
            "recommendation": "apply",
            "cv_pdf_path": None,
            "cover_letter_pdf_path": str(cover1),
        },
        # 5. Evaluation failed (should be excluded)
        {
            "success": False,
            "url": "https://boards.greenhouse.io/example/jobs/1005",
            "company": "Failed Corp",
            "title": "Unknown",
            "global_score": 0,
            "recommendation": "skip",
        },
        # 6. Lever job with external_job_id (should be exported)
        {
            "success": True,
            "url": "https://jobs.lever.co/techco/12345",
            "company": "TechCo",
            "title": "Product Manager",
            "global_score": 3.8,
            "recommendation": "apply",
            "cv_pdf_path": str(cv2),
            "cover_letter_pdf_path": str(cover2),
            "external_job_id": "lever-12345",
        },
    ]


# ---------------------------------------------------------------------------
# Candidate profile extraction
# ---------------------------------------------------------------------------


class TestExtractCandidateProfile:
    def test_full_name_split(self, sample_profile_snapshot) -> None:
        assert sample_profile_snapshot["first_name"] == "Mohamed"
        assert sample_profile_snapshot["last_name"] == "Abd Elrhman Azzam"
        assert sample_profile_snapshot["full_name"] == "Mohamed Abd Elrhman Azzam"

    def test_email_phone(self, sample_profile_snapshot) -> None:
        assert sample_profile_snapshot["email"] == "mohammed.abd.elrhman.azzam@gmail.com"
        assert sample_profile_snapshot["phone"] == "+49 152 5617 2336"

    def test_location_split(self, sample_profile_snapshot) -> None:
        assert sample_profile_snapshot["city"] == "Erlangen"
        assert sample_profile_snapshot["country"] == "Germany"

    def test_linkedin_github(self, sample_profile_snapshot) -> None:
        assert "linkedin.com/in/mohamed-azzam" in sample_profile_snapshot["linkedin_url"]
        assert "github.com/MohamedAzzam4" in sample_profile_snapshot["github_url"]

    def test_empty_profile(self) -> None:
        snap = extract_candidate_profile_snapshot({})
        assert snap["first_name"] == ""
        assert snap["email"] == ""
        assert snap["requires_sponsorship"] is False

    def test_location_without_comma(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"location": "Berlin"}}
        )
        assert snap["city"] == "Berlin"
        assert snap["country"] == ""


# ---------------------------------------------------------------------------
# Platform/source detection
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://jobs.siemens.com/jobs/123", "siemens"),
            ("https://boards.greenhouse.io/example/jobs/456", "greenhouse"),
            ("https://company.greenhouse.io/jobs/789", "greenhouse"),
            ("https://jobs.lever.co/techco/abc", "lever"),
            ("https://globalcorp.myworkdayjobs.com/jobs/1", "workday"),
            ("https://careers.smartrecruiters.com/co/jobs/2", "smartrecruiters"),
            ("https://www.linkedin.com/jobs/view/999", "linkedin_easy_apply"),
            ("https://example.com/jobs/1", "unknown"),
        ],
    )
    def test_detect(self, url: str, expected: str) -> None:
        assert _detect_platform(url) == expected


class TestDetectSource:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.linkedin.com/jobs/view/1", "linkedin"),
            ("https://indeed.com/viewjob?jk=abc", "indeed"),
            ("https://jobs.siemens.com/jobs/1", "siemens"),
            ("https://boards.greenhouse.io/co/jobs/1", "greenhouse"),
            ("https://example.com/jobs/1", "unknown"),
        ],
    )
    def test_detect(self, url: str, expected: str) -> None:
        assert _detect_source(url) == expected


# ---------------------------------------------------------------------------
# Application ID computation
# ---------------------------------------------------------------------------


class TestComputeApplicationId:
    def test_id_is_deterministic_for_same_inputs(self) -> None:
        id1 = _compute_application_id("greenhouse", "ext-1", "https://boards.greenhouse.io/co/jobs/1")
        id2 = _compute_application_id("greenhouse", "ext-1", "https://boards.greenhouse.io/co/jobs/1")
        assert id1 == id2
        assert len(id1) == 64  # sha256 hexdigest

    def test_id_changes_with_external_job_id(self) -> None:
        id1 = _compute_application_id("greenhouse", "ext-1", "https://boards.greenhouse.io/co/jobs/1")
        id2 = _compute_application_id("greenhouse", "ext-2", "https://boards.greenhouse.io/co/jobs/1")
        assert id1 != id2

    def test_id_falls_back_to_url_when_no_external_id(self) -> None:
        id1 = _compute_application_id("greenhouse", None, "https://boards.greenhouse.io/co/jobs/1")
        id2 = _compute_application_id("greenhouse", "", "https://boards.greenhouse.io/co/jobs/1")
        # Both fall back to URL-based identity.
        assert id1 == id2

    def test_id_ignores_utm_params(self) -> None:
        id1 = _compute_application_id("greenhouse", None, "https://boards.greenhouse.io/co/jobs/1")
        id2 = _compute_application_id(
            "greenhouse", None, "https://boards.greenhouse.io/co/jobs/1?utm_source=linkedin"
        )
        assert id1 == id2  # utm_ params stripped

    def test_id_ignores_trailing_slash(self) -> None:
        id1 = _compute_application_id("greenhouse", None, "https://boards.greenhouse.io/co/jobs/1")
        id2 = _compute_application_id("greenhouse", None, "https://boards.greenhouse.io/co/jobs/1/")
        assert id1 == id2


# ---------------------------------------------------------------------------
# build_queue_rows
# ---------------------------------------------------------------------------


class TestBuildQueueRows:
    def test_filters_below_threshold(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        # Job 1002 has score 2.0 -> excluded
        assert "https://boards.greenhouse.io/example/jobs/1002" not in urls

    def test_filters_skip_german(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        assert "https://boards.greenhouse.io/example/jobs/1003" not in urls

    def test_filters_missing_cv(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        assert "https://boards.greenhouse.io/example/jobs/1004" not in urls

    def test_filters_failed_evaluations(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        assert "https://boards.greenhouse.io/example/jobs/1005" not in urls

    def test_includes_valid_jobs(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        assert "https://boards.greenhouse.io/example/jobs/1001" in urls
        assert "https://jobs.lever.co/techco/12345" in urls

    def test_all_rows_have_required_fields(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        for row in rows:
            assert "application_id" in row and len(row["application_id"]) == 64
            assert "platform" in row
            assert "source" in row
            assert "company" in row
            assert "title" in row
            assert "url" in row
            assert "cv_pdf" in row and Path(row["cv_pdf"]).is_absolute()
            assert "cover_letter_pdf" in row and Path(row["cover_letter_pdf"]).is_absolute()
            assert row["verdict"] == "apply"
            assert row["status"] == "ready_to_apply"
            assert "metadata" in row
            assert "candidate_profile" in row["metadata"]

    def test_candidate_profile_embedded(self, sample_evaluations, sample_profile_snapshot) -> None:
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=3.5)
        for row in rows:
            snap = row["metadata"]["candidate_profile"]
            assert snap["email"] == "mohammed.abd.elrhman.azzam@gmail.com"
            assert snap["full_name"] == "Mohamed Abd Elrhman Azzam"

    def test_idempotent_no_duplicates(self, sample_evaluations, sample_profile_snapshot) -> None:
        # If two evaluations have the same URL, only one row should be exported.
        dup_eval = list(sample_evaluations)
        dup_eval.append({
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1001",  # same URL as job 1
            "company": "Dup Corp",
            "title": "Dup",
            "global_score": 4.9,
            "recommendation": "apply",
            "cv_pdf_path": dup_eval[0]["cv_pdf_path"],
            "cover_letter_pdf_path": dup_eval[0]["cover_letter_pdf_path"],
        })
        rows = build_queue_rows(dup_eval, {}, sample_profile_snapshot, threshold=3.5)
        urls = [r["url"] for r in rows]
        assert urls.count("https://boards.greenhouse.io/example/jobs/1001") == 1

    def test_threshold_override(self, sample_evaluations, sample_profile_snapshot) -> None:
        # With threshold 4.0, the 3.8-score Lever job is excluded.
        rows = build_queue_rows(sample_evaluations, {}, sample_profile_snapshot, threshold=4.0)
        urls = [r["url"] for r in rows]
        assert "https://jobs.lever.co/techco/12345" not in urls
        assert "https://boards.greenhouse.io/example/jobs/1001" in urls  # score 4.5


# ---------------------------------------------------------------------------
# export_queue (full end-to-end)
# ---------------------------------------------------------------------------


class TestExportQueue:
    def test_export_writes_jsonl(
        self, tmp_path: Path, sample_evaluations, sample_profile
    ) -> None:
        # Write inputs to tmp_path.
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps(sample_evaluations), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(sample_profile), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        summary = export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nonexistent.md",  # no pipeline.md
            profile_path=profile_path,
            threshold=3.5,
        )

        assert summary["exported"] == 2  # only jobs 1001 and lever
        assert output_path.exists()
        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            row = json.loads(line)
            assert row["verdict"] == "apply"
            assert row["status"] == "ready_to_apply"

    def test_export_is_idempotent(
        self, tmp_path: Path, sample_evaluations, sample_profile
    ) -> None:
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps(sample_evaluations), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(sample_profile), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        # Export twice.
        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )
        first_content = output_path.read_text(encoding="utf-8")
        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )
        second_content = output_path.read_text(encoding="utf-8")
        # exported_at will differ, so compare row-by-row ignoring that field.
        first_rows = [json.loads(l) for l in first_content.strip().splitlines()]
        second_rows = [json.loads(l) for l in second_content.strip().splitlines()]
        assert len(first_rows) == len(second_rows)
        for r1, r2 in zip(first_rows, second_rows):
            r1["metadata"].pop("exported_at", None)
            r2["metadata"].pop("exported_at", None)
            assert r1 == r2

    def test_export_uses_profile_threshold(
        self, tmp_path: Path, sample_evaluations
    ) -> None:
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps(sample_evaluations), encoding="utf-8")
        profile = {"evaluation": {"auto_cv_threshold": 4.0}}
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        summary = export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            # threshold=None -> reads from profile.yml
        )
        # threshold 4.0 excludes the 3.8 Lever job.
        assert summary["exported"] == 1
        assert summary["threshold"] == 4.0


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


class TestLoadProfile:
    def test_load_existing(self, tmp_path: Path, sample_profile) -> None:
        path = tmp_path / "profile.yml"
        path.write_text(yaml.safe_dump(sample_profile), encoding="utf-8")
        loaded = load_profile(path)
        assert loaded["candidate"]["full_name"] == "Mohamed Abd Elrhman Azzam"

    def test_load_missing(self, tmp_path: Path) -> None:
        loaded = load_profile(tmp_path / "nonexistent.yml")
        assert loaded == {}
