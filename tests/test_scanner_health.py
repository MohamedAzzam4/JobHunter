"""Hermetic tests for scanner failure classification and discovery health.

Covers the discovery-recovery contract:

A. valid empty scanner response -> SUCCESS_EMPTY
B. HTTP 422 -> REQUEST_CONTRACT_FAILURE
C. HTTP 404 -> REQUEST_CONTRACT_FAILURE
D. dependency/runtime crash -> DEPENDENCY_FAILURE
E. JobSpy result parser works without network (stubbed scrape output)
F. websearch career-link extraction -> JobHunter JobPosting
G. websearch empty page -> SUCCESS_EMPTY
H. LinkedIn-only -> SOURCE_ONLY -> not pilot-eligible
I. direct ATS hosts -> DIRECT_ATS -> pilot-eligible
J. one qualifying evaluation -> export exactly one row (DIRECT_ATS)
K. Siemens rows are never pilot-eligible

Plus: SmartRecruiters company-identifier derivation, human apply-URL
construction, HTTP-status mapping, and ScanResult note/finalize semantics.

All hermetic: httpx and scrape_jobs are stubbed, no network, no API keys.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanners.base import (
    ScanResult,
    ScanStatus,
    classify_exception,
    classify_http_status,
)
from scanners.jobspy_scanner import JobSpyScanner
from scanners.websearch_scanner import CareerSite, WebSearchScanner, extract_career_links
from scanners.workday import APIEndpoint, DirectAPIScanner
from utils.queue_exporter import build_queue_entries
from utils.target_quality import (
    CAREER_DETAIL_SAFE_APPLY,
    DIRECT_ATS,
    SOURCE_ONLY,
    UNKNOWN,
    classify_target_quality,
    is_pilot_eligible,
)

SUCCESS_EMPTY = ScanStatus.SUCCESS_EMPTY.value
SUCCESS_WITH_RESULTS = ScanStatus.SUCCESS_WITH_RESULTS.value
REQUEST_CONTRACT_FAILURE = ScanStatus.REQUEST_CONTRACT_FAILURE.value
DEPENDENCY_FAILURE = ScanStatus.DEPENDENCY_FAILURE.value


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResp:
    """Minimal httpx response stub."""

    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _fake_client_class(handler):
    """Build an httpx.AsyncClient stub dispatching to handler(method, url)."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            return handler("POST", url, json or {})

        async def get(self, url, params=None, headers=None):
            return handler("GET", url, params or {})

    return FakeClient


def _workday_config():
    return {
        "tracked_companies": [
            {
                "name": "Bosch",
                "careers_url": "https://www.bosch.com/careers/",
                "scan_method": "workday",
                "search_terms": ["working student"],
                "enabled": True,
            }
        ]
    }


def _sr_item(posting_id="744000147175004", identifier="BoschGroup"):
    return {
        "id": posting_id,
        "name": "Werkstudent Test Position",
        "company": {"identifier": identifier, "name": "Bosch Group"},
        "releasedDate": "2026-09-03T08:03:30.673Z",
        "location": {"city": "Nuremberg", "country": "de"},
        "ref": f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings/{posting_id}",
    }


# ---------------------------------------------------------------------------
# HTTP status mapping
# ---------------------------------------------------------------------------


class TestClassifyHttpStatus:
    def test_200_is_success(self):
        assert classify_http_status(200) is None

    def test_404_is_contract_failure(self):
        assert classify_http_status(404) == REQUEST_CONTRACT_FAILURE

    def test_422_is_contract_failure(self):
        assert classify_http_status(422) == REQUEST_CONTRACT_FAILURE

    def test_401_is_auth_failure(self):
        assert classify_http_status(401) == ScanStatus.AUTH_FAILURE.value

    def test_429_is_rate_limit(self):
        assert classify_http_status(429) == ScanStatus.RATE_LIMIT.value

    def test_500_is_network_failure(self):
        assert classify_http_status(500) == ScanStatus.NETWORK_FAILURE.value


class TestClassifyException:
    def test_import_error_is_dependency_failure(self):
        assert classify_exception(ImportError("no module")) == DEPENDENCY_FAILURE

    def test_numpy_crash_is_dependency_failure(self):
        err = OverflowError("cannot convert longdouble infinity to integer")
        assert classify_exception(err) == DEPENDENCY_FAILURE

    def test_timeout_is_network_failure(self):
        assert (
            classify_exception(httpx.TimeoutException("timed out"))
            == ScanStatus.NETWORK_FAILURE.value
        )

    def test_embedded_category_marker_is_transparent(self):
        err = RuntimeError("career page HTTP 403 [AUTH_FAILURE]")
        assert classify_exception(err) == ScanStatus.AUTH_FAILURE.value


class TestScanResultSemantics:
    def test_empty_no_errors_is_success_empty(self):
        result = ScanResult(scanner_name="x").finalize()
        assert result.status == SUCCESS_EMPTY
        assert result.error_category is None

    def test_jobs_win_over_errors(self):
        from scanners.base import JobPosting

        result = ScanResult(scanner_name="x")
        result.jobs.append(JobPosting(title="t", url="https://x.io/1", company="c"))
        result.note_error("boom", REQUEST_CONTRACT_FAILURE)
        result.finalize()
        assert result.status == SUCCESS_WITH_RESULTS

    def test_first_category_wins(self):
        result = ScanResult(scanner_name="x")
        result.note_error("a", REQUEST_CONTRACT_FAILURE)
        result.note_error("b", ScanStatus.NETWORK_FAILURE.value)
        assert result.error_category == REQUEST_CONTRACT_FAILURE
        assert result.finalize().status == REQUEST_CONTRACT_FAILURE


# ---------------------------------------------------------------------------
# A/B/C: DirectAPI scanner outcomes
# ---------------------------------------------------------------------------


class TestDirectAPIScannerOutcomes:
    def test_a_empty_200_is_success_empty(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(200, {"total": 0, "jobPostings": []})

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        scanner = DirectAPIScanner(_workday_config())
        result = asyncio.run(scanner.scan())
        assert result.status == SUCCESS_EMPTY
        assert result.job_count == 0

    def test_b_workday_422_is_contract_failure(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(422)

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        scanner = DirectAPIScanner(_workday_config())
        result = asyncio.run(scanner.scan())
        assert result.status == REQUEST_CONTRACT_FAILURE
        assert result.error_category == REQUEST_CONTRACT_FAILURE
        assert result.job_count == 0

    def test_c_smartrecruiters_404_is_contract_failure(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(404)

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        scanner = DirectAPIScanner(_workday_config())
        result = asyncio.run(scanner.scan())
        assert result.status == REQUEST_CONTRACT_FAILURE
        assert result.job_count == 0


def _workday_posting(**overrides):
    posting = {
        "title": "Werkstudent Test",
        "externalPath": "/job/Nuremberg/Werkstudent-Test_ID1",
        "locationsText": "Nuremberg",
        "bulletFields": ["ID1"],
        "postedOn": "Posted 1 Day Ago",
    }
    posting.update(overrides)
    return posting


class TestWorkdayLocationParsing:
    def test_locations_text_preferred_over_bullet_id(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(
                200, {"total": 1, "jobPostings": [_workday_posting()]}
            )

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        config = {
            "tracked_companies": [
                {
                    "name": "DATEV",
                    "careers_url": "https://www.datev.de/web/de/karriere/",
                    "scan_method": "workday",
                    "enabled": True,
                }
            ]
        }
        # DATEV has no entry in KNOWN_ENDPOINTS here; inject one directly.
        from scanners.workday import KNOWN_ENDPOINTS

        monkeypatch.setitem(
            KNOWN_ENDPOINTS,
            "datev",
            {
                "api_url": "https://datev.wd3.myworkdayjobs.com/wday/cxs/datev/Datev_Careers/jobs",
                "base_url": "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers",
                "provider": "workday",
            },
        )
        scanner = DirectAPIScanner(config)
        result = asyncio.run(scanner.scan())
        assert result.job_count == 1
        assert result.jobs[0].location == "Nuremberg"

    def test_bullet_fallback_without_locations_text(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(
                200,
                {
                    "total": 1,
                    "jobPostings": [
                        _workday_posting(locationsText="", bulletFields=["Berlin"])
                    ],
                },
            )

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        from scanners.workday import KNOWN_ENDPOINTS

        monkeypatch.setitem(
            KNOWN_ENDPOINTS,
            "datev",
            {
                "api_url": "https://datev.wd3.myworkdayjobs.com/wday/cxs/datev/Datev_Careers/jobs",
                "base_url": "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers",
                "provider": "workday",
            },
        )
        config = {
            "tracked_companies": [
                {
                    "name": "DATEV",
                    "careers_url": "https://www.datev.de/web/de/karriere/",
                    "scan_method": "workday",
                    "enabled": True,
                }
            ]
        }
        scanner = DirectAPIScanner(config)
        result = asyncio.run(scanner.scan())
        assert result.job_count == 1
        assert result.jobs[0].location == "Berlin"


# ---------------------------------------------------------------------------
# SmartRecruiters identifier + apply URL
# ---------------------------------------------------------------------------


class TestSmartRecruitersContract:
    def test_identifier_derived_from_api_url(self):
        endpoint = APIEndpoint(
            company_name="Bosch",
            api_url="https://careers.smartrecruiters.com/BoschGroup",
            base_url="https://www.bosch.com/careers/",
            provider="smartrecruiters",
        )
        assert endpoint.company_identifier == "BoschGroup"

    def test_request_uses_identifier_not_display_name(self, monkeypatch):
        seen_urls = []

        def handler(method, url, payload):
            seen_urls.append(url)
            return FakeResp(200, {"content": [], "totalFound": 0})

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        scanner = DirectAPIScanner(_workday_config())
        asyncio.run(scanner.scan())
        assert seen_urls, "expected at least one request"
        assert all("BoschGroup" in url for url in seen_urls)
        assert all("/companies/Bosch/" not in url for url in seen_urls)

    def test_apply_url_is_human_not_api(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(200, {"content": [_sr_item()], "totalFound": 1})

        monkeypatch.setattr(httpx, "AsyncClient", _fake_client_class(handler))
        scanner = DirectAPIScanner(_workday_config())
        result = asyncio.run(scanner.scan())
        assert result.status == SUCCESS_WITH_RESULTS
        assert result.job_count == 1
        job = result.jobs[0]
        assert job.url == (
            "https://jobs.smartrecruiters.com/BoschGroup/744000147175004"
        )
        assert "api.smartrecruiters.com" not in job.url
        assert classify_target_quality(job.url) == DIRECT_ATS


# ---------------------------------------------------------------------------
# D/E: JobSpy dependency handling + parser
# ---------------------------------------------------------------------------


class TestJobSpyScanner:
    def test_d_import_crash_is_dependency_failure(self, monkeypatch):
        """A broken dependency stack degrades, never crashes the scan."""
        scanner = JobSpyScanner({"jobspy_searches": []})

        def _boom(_config):
            raise ImportError("python-jobspy not installed")

        monkeypatch.setattr(scanner, "_do_search", _boom)
        scanner.searches = [{"term": "x", "location": "y"}]
        result = asyncio.run(scanner.scan())
        assert result.status == DEPENDENCY_FAILURE
        assert result.error_category == DEPENDENCY_FAILURE
        assert result.job_count == 0

    def test_e_parser_normalizes_stubbed_rows(self, monkeypatch):
        """The row parser works without network (stubbed frame)."""
        try:
            import jobspy as _jobspy
        except BaseException:
            pytest.skip("jobspy stack unavailable on this runtime")

        class StubFrame:
            empty = False

            def iterrows(self):
                yield 0, {
                    "job_url": "https://indeed.com/viewjob?jk=abc",
                    "title": "Werkstudent AI",
                    "company": "Acme",
                    "location": "Erlangen",
                    "description": "great job",
                    "date_posted": "2026-09-01",
                    "site": "indeed",
                    "min_amount": None,
                    "max_amount": None,
                }
                yield 1, {
                    "job_url": "nan",
                    "title": "Bad Row",
                    "company": "Acme",
                    "location": "",
                    "description": "",
                    "date_posted": "",
                    "site": "indeed",
                }

        # scrape_jobs is imported inside _do_search from the jobspy module;
        # patching the module attribute intercepts it (no network).
        monkeypatch.setattr(_jobspy, "scrape_jobs", lambda **kwargs: StubFrame())

        scanner = JobSpyScanner({"jobspy_searches": []})
        jobs = scanner._do_search(
            {"term": "Werkstudent", "location": "Erlangen, Germany"}
        )
        assert len(jobs) == 1
        assert jobs[0].title == "Werkstudent AI"
        assert jobs[0].source == "jobspy-indeed"


# ---------------------------------------------------------------------------
# F/G: websearch extraction
# ---------------------------------------------------------------------------


def _websearch_site():
    return CareerSite(
        company_name="Fraunhofer IIS",
        careers_url="https://www.iis.fraunhofer.de/en/jobs.html",
        source_tag="websearch-fraunhofer-iis",
    )


class TestWebSearchExtraction:
    def test_f_extracts_same_host_and_ats_links(self):
        html = """
        <html><body>
        <a href="/en/jobs/studierende/hiwi-123.html">Student Assistant AI</a>
        <a href="https://boards.greenhouse.io/acme/jobs/9">Greenhouse Job</a>
        <a href="https://www.linkedin.com/jobs/view/1">LinkedIn Job</a>
        <a href="/en/impressum.html">Imprint</a>
        <a href="https://cdn.example.com/logo.png">Logo</a>
        </body></html>
        """
        postings = extract_career_links(html, _websearch_site())
        by_url = {p.url: p for p in postings}
        assert "https://www.iis.fraunhofer.de/en/jobs/studierende/hiwi-123.html" in by_url
        assert "https://boards.greenhouse.io/acme/jobs/9" in by_url
        # Aggregator + non-job links are dropped.
        assert not any("linkedin.com" in url for url in by_url)
        assert not any("impressum" in url for url in by_url)
        assert not any(url.endswith(".png") for url in by_url)
        # Anchor text becomes the title (never invented).
        assert (
            by_url["https://www.iis.fraunhofer.de/en/jobs/studierende/hiwi-123.html"].title
            == "Student Assistant AI"
        )
        qualities = {p.target_quality for p in postings}
        assert DIRECT_ATS in qualities
        assert CAREER_DETAIL_SAFE_APPLY in qualities
        assert SOURCE_ONLY not in qualities

    def test_g_empty_page_extracts_nothing(self):
        postings = extract_career_links("<html><body><p>No jobs</p></body></html>", _websearch_site())
        assert postings == []

    def test_g_empty_config_scans_success_empty(self):
        scanner = WebSearchScanner({})
        result = asyncio.run(scanner.scan())
        assert result.status == SUCCESS_EMPTY
        assert result.job_count == 0


# ---------------------------------------------------------------------------
# H/I/K: target quality + pilot eligibility
# ---------------------------------------------------------------------------


class TestTargetQuality:
    def test_h_linkedin_is_source_only_not_eligible(self):
        url = "https://www.linkedin.com/jobs/view/999"
        assert classify_target_quality(url) == SOURCE_ONLY
        eligible, reason = is_pilot_eligible(
            {
                "platform": "linkedin_easy_apply",
                "source": "linkedin",
                "url": url,
                "status": "ready_to_apply",
                "cv_pdf": "/tmp/cv.pdf",
                "cover_letter_pdf": "/tmp/cover.pdf",
            }
        )
        assert not eligible
        assert "SOURCE_ONLY" in reason

    def test_i_direct_ats_hosts_are_eligible(self):
        for url in [
            "https://boards.greenhouse.io/acme/jobs/1",
            "https://jobs.lever.co/acme/abc",
            "https://jobs.ashby.com/acme/xyz",
            "https://acme.myworkdayjobs.com/en-US/acme/jobs/1",
            "https://jobs.smartrecruiters.com/BoschGroup/123",
        ]:
            assert classify_target_quality(url) == DIRECT_ATS, url
            eligible, _ = is_pilot_eligible(
                {
                    "platform": "generic",
                    "source": "test",
                    "url": url,
                    "target_quality": DIRECT_ATS,
                    "status": "ready_to_apply",
                    "cv_pdf": "/tmp/cv.pdf",
                    "cover_letter_pdf": "/tmp/cover.pdf",
                }
            )
            assert eligible, url

    def test_unknown_is_not_eligible(self):
        eligible, _ = is_pilot_eligible(
            {
                "platform": "generic",
                "source": "test",
                "url": "https://example.com/blog/post",
                "target_quality": UNKNOWN,
                "status": "ready_to_apply",
                "cv_pdf": "/tmp/cv.pdf",
                "cover_letter_pdf": "/tmp/cover.pdf",
            }
        )
        assert not eligible

    def test_k_siemens_never_eligible(self):
        eligible, reason = is_pilot_eligible(
            {
                "platform": "siemens",
                "source": "siemens",
                "url": "https://jobs.siemens.com/careers/123",
                "target_quality": DIRECT_ATS,
                "status": "ready_to_apply",
                "cv_pdf": "/tmp/cv.pdf",
                "cover_letter_pdf": "/tmp/cover.pdf",
            }
        )
        assert not eligible
        assert "siemens" in reason.lower()


# ---------------------------------------------------------------------------
# J: one qualifying evaluation -> exactly one exported row (DIRECT_ATS)
# ---------------------------------------------------------------------------


def _make_pdfs(tmp_path):
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    cover.write_bytes(b"%PDF-1.4 fake")
    return str(cv), str(cover)


class TestSingleQualifyingExport:
    def test_j_one_qualifying_row_exports_exactly_one(self, tmp_path):
        cv_pdf, cover_pdf = _make_pdfs(tmp_path)
        evaluations = [
            {
                "success": True,
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "company": "Acme",
                "title": "Working Student AI",
                "location": "Erlangen",
                "global_score": 4.5,
                "recommendation": "apply",
                "cv_pdf_path": cv_pdf,
                "cover_letter_pdf_path": cover_pdf,
                "description": "great role",
            }
        ]
        rows, skipped = build_queue_entries(
            evaluations, {}, {"full_name": "Test User"}, threshold=3.5
        )
        assert len(rows) == 1
        assert not skipped
        row = rows[0]
        assert row["metadata"]["target_quality"] == DIRECT_ATS
        eligible, reason = is_pilot_eligible(row)
        assert eligible, reason
