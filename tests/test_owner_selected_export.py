"""Hermetic tests for owner-selected export mode (Phase 1.4).

A. apply + normal auto-export → exported
B. consider + no owner flag → not automatically exported
C. consider + explicit owner flag → exported if all other gates pass
D. not_recommended + owner flag → rejected
E. expired consider + owner flag → rejected APPLICATION_EXPIRED
F. SOURCE_ONLY consider + owner flag → rejected target_not_eligible
G. Siemens consider + owner flag → rejected siemens_blocked
H. consider + Docs from another application → rejected document_lineage_mismatch
I. owner-selected export preserves original: recommendation == consider
J. owner selection provenance survives queue/package export

All hermetic — no network except stubbed freshness, no PII, no real PDFs beyond tmp fakes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.queue_exporter import (
    APPLICATION_EXPIRED,
    DOCUMENT_LINEAGE_MISMATCH,
    NOT_RECOMMENDED,
    SIEMENS_BLOCKED,
    TARGET_NOT_ELIGIBLE,
    build_queue_entries,
    export_queue,
    _compute_application_id,
    _detect_platform,
)


@pytest.fixture
def tmp_profiles(tmp_path: Path):
    profile = {"candidate": {"full_name": "Test User", "email": "test@example.com", "location": "Erlangen, Germany"}}
    snap = {"full_name": "Test User", "email": "test@example.com", "city": "Erlangen", "country": "Germany"}
    return profile, snap


def make_pdfs(tmp_path: Path, n: int = 2):
    cvs = []
    covers = []
    for i in range(n):
        cv = tmp_path / f"cv{i}.pdf"
        cover = tmp_path / f"cover{i}.pdf"
        cv.write_bytes(b"%PDF cv")
        cover.write_bytes(b"%PDF cover")
        cvs.append(str(cv))
        covers.append(str(cover))
    return cvs, covers


class TestOwnerSelectedHermetic:
    def test_A_apply_auto_exported(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        evals = [{"success": True, "url": "https://boards.greenhouse.io/c/jobs/1", "company": "Co", "title": "Eng", "global_score": 4.5, "recommendation": "apply", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=False)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "apply"
        assert rows[0]["metadata"]["selection_source"] == "auto"

    def test_B_consider_not_auto_exported(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-X--m-w-d-_ID15374"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=False)
        assert len(rows) == 0
        assert any(s["reason"] == NOT_RECOMMENDED for s in skipped)

    def test_C_consider_with_owner_flag_exported(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-X--m-w-d-_ID15374"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "consider"
        assert rows[0]["metadata"]["owner_selected"] is True

    def test_D_not_recommended_with_owner_still_blocked(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-X--m-w-d-_ID15374"
        for rec in ("skip", "not_recommended", "skip_german"):
            evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": rec, "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
            rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
            assert len(rows) == 0, f"should block {rec}"
            assert any(s["reason"] == NOT_RECOMMENDED for s in skipped)

    def test_E_expired_consider_with_owner_rejected(self, tmp_path: Path, tmp_profiles, monkeypatch):
        profile, _ = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-Y--m-w-d-_ID99999"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        evals_path = tmp_path / "evals.json"
        evals_path.write_text(json.dumps(evals), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        output = tmp_path / "out.jsonl"

        class FakeResp:
            def __init__(self, status_code=200, payload=None):
                self.status_code = status_code
                self._payload = payload or {}
            def json(self):
                return self._payload

        def fake_client(handler):
            class FakeClient:
                def __init__(self, *a, **k): pass
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def post(self, url, json=None, headers=None): return handler("POST", url, json or {})
                def get(self, *a, **k): return FakeResp(404)
            return FakeClient

        def handler(method, url, payload):
            return FakeResp(200, {"total": 0, "jobPostings": []})

        monkeypatch.setattr(httpx, "Client", fake_client(handler))
        summary = export_queue(output_path=output, evaluations_path=evals_path, pipeline_path=tmp_path / "none.md", profile_path=profile_path, threshold=3.5, owner_selected=True, freshness_check=True)
        assert summary["exported"] == 0
        assert summary["skipped_reasons"].get(APPLICATION_EXPIRED, 0) == 1

    def test_F_source_only_blocked(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://www.linkedin.com/jobs/view/999999"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.2, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
        assert len(rows) == 0
        assert any(s["reason"] == TARGET_NOT_ELIGIBLE for s in skipped)

    def test_G_siemens_blocked(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://jobs.siemens.com/jobs/12345"
        evals = [{"success": True, "url": url, "company": "Siemens", "title": "Werkstudent", "global_score": 4.5, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
        assert len(rows) == 0
        assert any(s["reason"] == SIEMENS_BLOCKED for s in skipped)

    def test_H_cross_job_docs_rejected(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cv_shared = tmp_path / "shared.pdf"
        cover_shared = tmp_path / "shared_cover.pdf"
        cv_shared.write_bytes(b"shared")
        cover_shared.write_bytes(b"shared cover")
        url1 = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-A--m-w-d-_ID15374"
        url2 = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-B--m-w-d-_ID15375"
        evals = [
            {"success": True, "url": url1, "company": "Co", "title": "A", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": str(cv_shared), "cover_letter_pdf_path": str(cover_shared)},
            {"success": True, "url": url2, "company": "Co", "title": "B", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": str(cv_shared), "cover_letter_pdf_path": str(cover_shared)},
        ]
        rows, skipped = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
        assert len(rows) == 1
        assert any(s["reason"] == DOCUMENT_LINEAGE_MISMATCH for s in skipped)

    def test_I_preserves_original_recommendation(self, tmp_path: Path, tmp_profiles):
        _, snap = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-X--m-w-d-_ID15374"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0]}]
        rows, _ = build_queue_entries(evals, {}, snap, threshold=3.5, owner_selected=True)
        assert rows[0]["verdict"] == "consider"
        assert rows[0]["metadata"]["original_recommendation"] == "consider"

    def test_J_provenance_survives_export(self, tmp_path: Path, tmp_profiles):
        profile, _ = tmp_profiles
        cvs, covers = make_pdfs(tmp_path)
        url = "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/Werkstudent-X--m-w-d-_ID15374"
        evals = [{"success": True, "url": url, "company": "Co", "title": "Eng", "global_score": 4.0, "recommendation": "consider", "cv_pdf_path": cvs[0], "cover_letter_pdf_path": covers[0], "description": "desc"}]
        evals_path = tmp_path / "evals.json"
        evals_path.write_text(json.dumps(evals), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        output = tmp_path / "out.jsonl"
        platform = _detect_platform(url)
        aid = _compute_application_id(platform, None, url)
        summary = export_queue(output_path=output, evaluations_path=evals_path, pipeline_path=tmp_path / "none.md", profile_path=profile_path, threshold=3.5, owner_selected=True, owner_application_id=aid, freshness_check=False)
        assert summary["exported"] == 1
        row = json.loads(output.read_text(encoding="utf-8").strip().splitlines()[0])
        assert row["metadata"]["selection_source"] == "owner"
        assert row["metadata"]["owner_selected"] is True
        assert row["verdict"] == "consider"
        assert row["application_id"] == aid
