"""Hermetic tests for export-time target freshness validation.

A. Workday live requisition -> row kept (supervisor-ready).
B. Workday positively expired requisition -> excluded, APPLICATION_EXPIRED.
C. Transient/network failure -> NOT expired (row kept, UNKNOWN).
D. Freshness metadata (evaluated_at, target_quality, external_job_id)
   survives a normal export.

Plus: SmartRecruiters 200/404 mapping, identity parsing, unknown-host
dispatch, and export_queue integration with freshness_check on/off.

All hermetic: httpx.Client is stubbed, no network, no API keys.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.freshness import (
    APPLICATION_EXPIRED,
    EXPIRED,
    LIVE,
    UNKNOWN,
    check_smartrecruiters,
    check_target_freshness,
    check_workday,
    filter_fresh_rows,
    parse_workday_identity,
)
from utils.queue_exporter import build_queue_entries, export_queue

LIVE_URL = (
    "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/"
    "Nuremberg/Werkstudent-X--m-w-d-_ID15374"
)
EXPIRED_URL = (
    "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/"
    "Nuremberg/Werkstudent-Y--m-w-d-_ID15339"
)
SR_LIVE_URL = "https://jobs.smartrecruiters.com/BoschGroup/744000147175004"
SR_DEAD_URL = "https://jobs.smartrecruiters.com/BoschGroup/000000000000000"


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _fake_sync_client(handler):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            return handler("POST", url, json or {})

        def get(self, url, headers=None):
            return handler("GET", url, {})

    return FakeClient


def test_parse_workday_identity():
    tenant, board, req_id = parse_workday_identity(LIVE_URL)
    assert tenant == "datev"
    assert board == "Datev_Careers"
    assert req_id == "ID15374"


def test_parse_workday_identity_rejects_non_workday():
    assert parse_workday_identity("https://example.com/jobs/1") is None
    assert parse_workday_identity("not-a-url") is None
    assert (
        parse_workday_identity("https://datev.wd3.myworkdayjobs.com/de-DE/other")
        is None
    )


def _workday_handler(postings_by_id):
    def handler(method, url, payload):
        term = (payload.get("searchText", "") or "")
        hits = [
            p for rid, p in postings_by_id.items()
            if rid in term
        ]
        return FakeResp(200, {"total": len(hits), "jobPostings": hits})

    return handler


def _posting(external_path):
    return {"title": "Werkstudent X", "externalPath": external_path}


class TestCheckWorkday:
    def test_live_requisition(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(_workday_handler({"ID15374": _posting("/job/Nuremberg/X_ID15374")})),
        )
        status, _ = check_workday(LIVE_URL)
        assert status == LIVE

    def test_expired_requisition(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client", _fake_sync_client(_workday_handler({}))
        )
        status, detail = check_workday(EXPIRED_URL)
        assert status == EXPIRED
        assert "ID15339" in detail

    def test_transport_failure_is_unknown(self, monkeypatch):
        class BoomClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *a, **k):
                raise httpx.ConnectError("no route")

            def get(self, *a, **k):
                raise httpx.ConnectError("no route")

        monkeypatch.setattr(httpx, "Client", BoomClient)
        status, _ = check_workday(LIVE_URL)
        assert status == UNKNOWN

    def test_http_500_is_unknown(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(lambda m, u, p: FakeResp(500)),
        )
        status, _ = check_workday(LIVE_URL)
        assert status == UNKNOWN


class TestCheckSmartRecruiters:
    def test_200_is_live(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(lambda m, u, p: FakeResp(200, {"id": "1"})),
        )
        status, _ = check_smartrecruiters(SR_LIVE_URL)
        assert status == LIVE

    def test_404_is_expired(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(lambda m, u, p: FakeResp(404)),
        )
        status, _ = check_smartrecruiters(SR_DEAD_URL)
        assert status == EXPIRED


class TestDispatch:
    def test_unknown_host_is_unknown(self):
        status, detail = check_target_freshness("https://example.com/jobs/1")
        assert status == UNKNOWN
        assert detail

    def test_unparseable_is_unknown(self):
        status, _ = check_target_freshness("not-a-url")
        assert status == UNKNOWN


def _row(url, company="DATEV"):
    return {
        "application_id": "a" * 64,
        "company": company,
        "title": "Werkstudent X",
        "url": url,
        "platform": "workday",
        "source": "workday",
        "status": "ready_to_apply",
        "cv_pdf": "/tmp/cv.pdf",
        "cover_letter_pdf": "/tmp/cover.pdf",
    }


class TestFilterFreshRows:
    def test_a_live_row_kept(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(_workday_handler({"ID15374": _posting("/job/Nuremberg/X_ID15374")})),
        )
        fresh, skipped = filter_fresh_rows([_row(LIVE_URL)])
        assert len(fresh) == 1
        assert skipped == []

    def test_b_expired_row_excluded(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client", _fake_sync_client(_workday_handler({}))
        )
        fresh, skipped = filter_fresh_rows([_row(EXPIRED_URL)])
        assert fresh == []
        assert len(skipped) == 1
        assert skipped[0]["reason"] == APPLICATION_EXPIRED
        assert skipped[0]["url"] == EXPIRED_URL

    def test_c_transient_failure_keeps_row(self, monkeypatch):
        class BoomClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *a, **k):
                raise httpx.TimeoutException("timed out")

            def get(self, *a, **k):
                raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx, "Client", BoomClient)
        fresh, skipped = filter_fresh_rows([_row(LIVE_URL)])
        assert len(fresh) == 1
        assert skipped == []


def _make_pdfs(tmp_path):
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    cover.write_bytes(b"%PDF-1.4 fake")
    return str(cv), str(cover)


class TestExportFreshnessIntegration:
    def _evaluation(self, tmp_path, url):
        cv_pdf, cover_pdf = _make_pdfs(tmp_path)
        return {
            "success": True,
            "url": url,
            "company": "DATEV",
            "title": "Werkstudent X",
            "location": "Nuremberg",
            "global_score": 4.8,
            "recommendation": "apply",
            "cv_pdf_path": cv_pdf,
            "cover_letter_pdf_path": cover_pdf,
            "description": "great role",
            "evaluated_at": "2026-09-04T00:00:00",
        }

    def test_d_metadata_survives_export(self, tmp_path, monkeypatch):
        # Freshness disabled: pure export path, metadata intact.
        evaluations = [self._evaluation(tmp_path, "https://boards.greenhouse.io/acme/jobs/1")]
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(
            "candidate:\n  full_name: Test User\n"
            "evaluation:\n  auto_cv_threshold: 3.5\n",
            encoding="utf-8",
        )
        out = tmp_path / "queue.jsonl"
        from utils.queue_exporter import export_queue as _export_queue

        summary = _export_queue(
            output_path=out,
            evaluations_path=_write_evals(tmp_path, evaluations),
            pipeline_path=_write_pipeline(tmp_path),
            profile_path=profile_path,
            threshold=3.5,
            freshness_check=False,
        )
        assert summary["exported"] == 1
        import json as _json

        row = _json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert row["evaluated_at"] == "2026-09-04T00:00:00"
        assert row["metadata"]["target_quality"] == "DIRECT_ATS"
        assert "external_job_id" in row

    def test_expired_excluded_with_check_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client", _fake_sync_client(_workday_handler({}))
        )
        evaluations = [self._evaluation(tmp_path, EXPIRED_URL)]
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(
            "candidate:\n  full_name: Test User\n"
            "evaluation:\n  auto_cv_threshold: 3.5\n",
            encoding="utf-8",
        )
        out = tmp_path / "queue.jsonl"
        from utils.queue_exporter import export_queue as _export_queue

        summary = _export_queue(
            output_path=out,
            evaluations_path=_write_evals(tmp_path, evaluations),
            pipeline_path=_write_pipeline(tmp_path, url=EXPIRED_URL),
            profile_path=profile_path,
            threshold=3.5,
            freshness_check=True,
        )
        assert summary["exported"] == 0
        assert summary["skipped_reasons"] == {APPLICATION_EXPIRED: 1}

    def test_live_kept_with_check_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client",
            _fake_sync_client(_workday_handler({"ID15374": _posting("/job/Nuremberg/X_ID15374")})),
        )
        evaluations = [self._evaluation(tmp_path, LIVE_URL)]
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(
            "candidate:\n  full_name: Test User\n"
            "evaluation:\n  auto_cv_threshold: 3.5\n",
            encoding="utf-8",
        )
        out = tmp_path / "queue.jsonl"
        from utils.queue_exporter import export_queue as _export_queue

        summary = _export_queue(
            output_path=out,
            evaluations_path=_write_evals(tmp_path, evaluations),
            pipeline_path=_write_pipeline(tmp_path, url=LIVE_URL),
            profile_path=profile_path,
            threshold=3.5,
            freshness_check=True,
        )
        assert summary["exported"] == 1


def _write_evals(tmp_path, evaluations):
    import json as _json

    path = tmp_path / "evaluations.json"
    path.write_text(_json.dumps(evaluations), encoding="utf-8")
    return path


def _write_pipeline(tmp_path, url="https://boards.greenhouse.io/acme/jobs/1"):
    path = tmp_path / "pipeline.md"
    path.write_text(
        "# Job Pipeline\n\n## Pending\n"
        f"- [ ] {url} | DATEV | Werkstudent X [Nuremberg]\n",
        encoding="utf-8",
    )
    return path
