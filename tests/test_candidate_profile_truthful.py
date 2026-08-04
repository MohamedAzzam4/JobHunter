"""Tests for the truthful candidate-profile snapshot policy.

The exporter must NEVER fabricate candidate facts. A partial snapshot is
safe (UAA's CandidateProfile has optional fields and a fallback profile),
but an invented ``False`` sponsorship answer or invented fields like
``website`` / ``work_authorization`` / ``years_of_experience`` could make
UAA answer questions without evidence.

Policy under test:
- Emit ONLY values explicitly present in the JobHunter profile data.
- Absent/null/blank/whitespace-only personal values are omitted.
- ``requires_sponsorship`` is preserved exactly when the source profile
  explicitly stores a Python ``bool``; a truthy string is NOT coerced; when
  absent it is omitted (never exported as ``False``).
- ``website``, ``work_authorization``, ``years_of_experience`` are NEVER
  invented.
- Real values (name, email, phone, LinkedIn, GitHub, city/country, current
  position) are kept; first/last and city/country are derived from present
  full_name/location.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.queue_exporter import (  # noqa: E402
    extract_candidate_profile_snapshot,
    export_queue,
)

NEVER_INVENTED = ("website", "work_authorization", "years_of_experience", "salutation", "academic_title")


class TestCandidateProfileTruthful:
    def test_empty_profile_yields_empty_snapshot(self) -> None:
        assert extract_candidate_profile_snapshot({}) == {}

    def test_absent_sponsorship_is_omitted_never_false(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "email": "t@example.com"}}
        )
        assert "requires_sponsorship" not in snap

    def test_explicit_false_is_preserved(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "requires_sponsorship": False}}
        )
        assert snap["requires_sponsorship"] is False

    def test_explicit_true_is_preserved(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "requires_sponsorship": True}}
        )
        assert snap["requires_sponsorship"] is True

    def test_string_sponsorship_not_coerced(self) -> None:
        # A truthy string is NOT evidence of sponsorship -> omitted.
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "requires_sponsorship": "Yes"}}
        )
        assert "requires_sponsorship" not in snap

    def test_blank_strings_are_omitted(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {
                "candidate": {
                    "full_name": "Test User",
                    "email": "",
                    "phone": "   ",
                    "linkedin": "  ",
                    "github": "",
                    "location": "",
                    "subtitle": None,
                }
            }
        )
        assert snap["full_name"] == "Test User"
        for key in ("email", "phone", "linkedin_url", "github_url", "city", "country", "current_position"):
            assert key not in snap

    def test_blank_full_name_omitted(self) -> None:
        assert extract_candidate_profile_snapshot({"candidate": {"full_name": "   "}}) == {}

    def test_partial_profile_has_no_invented_values(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {"candidate": {"full_name": "Test User", "email": "t@example.com"}}
        )
        for key in NEVER_INVENTED:
            assert key not in snap

    def test_real_values_kept_when_present(self) -> None:
        snap = extract_candidate_profile_snapshot(
            {
                "candidate": {
                    "full_name": "Mohamed Abd Elrhman Azzam",
                    "email": "m@example.com",
                    "phone": "+49 152 000",
                    "linkedin": "https://linkedin.com/in/ma",
                    "github": "https://github.com/MohamedAzzam4",
                    "location": "Erlangen, Germany",
                    "subtitle": "AI Engineer",
                }
            }
        )
        assert snap["full_name"] == "Mohamed Abd Elrhman Azzam"
        assert snap["first_name"] == "Mohamed"
        assert snap["last_name"] == "Abd Elrhman Azzam"
        assert snap["email"] == "m@example.com"
        assert snap["phone"] == "+49 152 000"
        assert snap["linkedin_url"] == "https://linkedin.com/in/ma"
        assert snap["github_url"] == "https://github.com/MohamedAzzam4"
        assert snap["city"] == "Erlangen"
        assert snap["country"] == "Germany"
        assert snap["current_position"] == "AI Engineer"

    def test_location_without_comma(self) -> None:
        snap = extract_candidate_profile_snapshot({"candidate": {"location": "Berlin"}})
        assert snap["city"] == "Berlin"
        assert "country" not in snap


class TestCandidateProfileInExportedRow:
    """An exported queue row must never carry a fabricated sponsorship answer."""

    def test_row_without_sponsorship_data_has_no_sponsorship_key(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")

        evaluation = {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1001",
            "company": "Acme",
            "title": "Engineer",
            "global_score": 4.5,
            "recommendation": "apply",
            "cv_pdf_path": str(cv),
            "cover_letter_pdf_path": str(cover),
        }
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps([evaluation]), encoding="utf-8")
        # Candidate profile has NO sponsorship data.
        profile = {"candidate": {"full_name": "Test User", "email": "t@example.com"}}
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )

        rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        snapshot = rows[0]["metadata"]["candidate_profile"]
        assert "requires_sponsorship" not in snapshot
        for key in NEVER_INVENTED:
            assert key not in snapshot
        assert snapshot["full_name"] == "Test User"
        assert snapshot["email"] == "t@example.com"

    def test_row_with_explicit_false_sponsorship_preserves_false(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")

        evaluation = {
            "success": True,
            "url": "https://boards.greenhouse.io/example/jobs/1002",
            "company": "Acme",
            "title": "PM",
            "global_score": 4.2,
            "recommendation": "apply",
            "cv_pdf_path": str(cv),
            "cover_letter_pdf_path": str(cover),
        }
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps([evaluation]), encoding="utf-8")
        profile = {"candidate": {"full_name": "Test User", "requires_sponsorship": False}}
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"

        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )

        rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        snapshot = rows[0]["metadata"]["candidate_profile"]
        assert snapshot["requires_sponsorship"] is False