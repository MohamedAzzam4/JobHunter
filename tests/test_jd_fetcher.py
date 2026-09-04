"""Tests for the JD fetcher LinkedIn extraction."""
import asyncio

import httpx
import pytest

from utils.jd_fetcher import (
    _clean_html_to_text,
    _extract_jsonld_jd,
    _extract_linkedin_jd,
    _smartrecruiters_api_detail,
    fetch_jd,
)


class TestLinkedInExtractor:
    """Unit tests for _extract_linkedin_jd (no network needed)."""

    def test_extracts_from_markup_div(self):
        html = '''
        <div class="show-more-less-html__markup relative overflow-hidden">
            <p>We are looking for a <strong>Working Student</strong>.</p>
            <ul><li>Python</li><li>Data analysis</li></ul>
        </div>
        '''
        text = _extract_linkedin_jd(html)
        assert "Working Student" in text
        assert "Python" in text
        assert "Data analysis" in text
        assert "<" not in text  # No HTML tags in output

    def test_returns_empty_on_missing_div(self):
        html = '<div class="other-content">No JD here</div>'
        assert _extract_linkedin_jd(html) == ""

    def test_returns_empty_on_empty_div(self):
        html = '<div class="show-more-less-html__markup"></div>'
        assert _extract_linkedin_jd(html) == ""

    def test_handles_extra_css_classes(self):
        """LinkedIn may add extra classes like 'relative overflow-hidden'."""
        html = '''
        <div class="show-more-less-html__markup relative overflow-hidden"
             data-max-lines="6">
            <p>Job description content here with details.</p>
        </div>
        '''
        text = _extract_linkedin_jd(html)
        assert "Job description content here" in text

    def test_strips_html_entities(self):
        html = '''
        <div class="show-more-less-html__markup">
            C++ &amp; Python &gt; Java
        </div>
        '''
        text = _extract_linkedin_jd(html)
        assert "C++ & Python > Java" in text

    def test_handles_nested_html(self):
        html = '''
        <div class="show-more-less-html__markup">
            <h3>Requirements</h3>
            <ul>
                <li><strong>B.Sc.</strong> in Computer Science</li>
                <li>Experience with <em>PyTorch</em></li>
            </ul>
        </div>
        '''
        text = _extract_linkedin_jd(html)
        assert "B.Sc." in text
        assert "Computer Science" in text
        assert "PyTorch" in text


class TestCleanHtmlToText:
    """Unit tests for _clean_html_to_text."""

    def test_removes_script_tags(self):
        html = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        text = _clean_html_to_text(html)
        assert "alert" not in text
        assert "Hello" in text
        assert "World" in text

    def test_removes_style_tags(self):
        html = '<style>.body{color:red}</style><p>Content</p>'
        text = _clean_html_to_text(html)
        assert "color" not in text
        assert "Content" in text

    def test_decodes_entities(self):
        html = "AT&amp;T &lt;100&gt; employees &quot;here&quot;"
        text = _clean_html_to_text(html)
        assert "AT&T" in text
        assert "<100>" in text

    def test_collapses_whitespace(self):
        html = "<p>  lots   of     spaces  </p>"
        text = _clean_html_to_text(html)
        assert "  " not in text  # No double spaces


class TestJsonLdExtraction:
    """Unit tests for _extract_jsonld_jd (no network needed)."""

    def test_extracts_jobposting_description(self):
        html = (
            "<html><head><title>Thin Shell</title></head><body>"
            '<div id="root"></div>'
            '<script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "Werkstudent AI", '
            '"description": "<p>' + ("Great role with Python. " * 30) + "</p>\"}"
            "</script></body></html>"
        )
        title, text = _extract_jsonld_jd(html)
        assert title == "Werkstudent AI"
        assert "Great role with Python" in text
        assert "<" not in text

    def test_ignores_non_jobposting_blocks(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type": "WebSite", "description": "' + ("x" * 500) + '"}'
            "</script>"
        )
        assert _extract_jsonld_jd(html) == ("", "")

    def test_returns_empty_without_blocks(self):
        assert _extract_jsonld_jd("<html><body>plain</body></html>") == ("", "")

    def test_rejects_short_descriptions(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type": "JobPosting", "description": "Too short"}'
            "</script>"
        )
        assert _extract_jsonld_jd(html) == ("", "")


class TestSmartRecruitersUrlSplit:
    def test_splits_apply_url(self):
        company, posting_id = _smartrecruiters_api_detail(
            "https://jobs.smartrecruiters.com/BoschGroup/744000147175004"
        )
        assert company == "BoschGroup"
        assert posting_id == "744000147175004"

    def test_strips_seo_slug(self):
        company, posting_id = _smartrecruiters_api_detail(
            "https://jobs.smartrecruiters.com/BoschGroup/744000147175004-werkstudent-x"
        )
        assert company == "BoschGroup"
        assert posting_id == "744000147175004"

    def test_rejects_non_sr_urls(self):
        assert _smartrecruiters_api_detail("https://example.com/jobs/1") == (None, None)
        assert _smartrecruiters_api_detail("not-a-url") == (None, None)


class _FakeHttpResp:
    def __init__(self, status_code=200, payload=None, text="", headers=None, url=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}
        self.url = url

    def json(self):
        return self._payload


def _fake_http_client(handler):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            return handler(url)

        async def post(self, url, **kwargs):
            return handler(url)

    return FakeClient


class TestFetchJdSmartRecruitersBranch:
    def test_detail_api_supplies_description(self, monkeypatch):
        sections = {
            "jobDescription": {"title": "Desc", "text": "<p>" + ("Build things. " * 40) + "</p>"},
            "qualifications": {"title": "Qual", "text": "<ul><li>Python</li></ul>"},
        }
        payload = {"name": "Werkstudent X", "jobAd": {"sections": sections}}

        def handler(url):
            assert "api.smartrecruiters.com" in url
            assert "BoschGroup" in url
            return _FakeHttpResp(200, payload)

        monkeypatch.setattr(httpx, "AsyncClient", _fake_http_client(handler))
        result = asyncio.run(fetch_jd("https://jobs.smartrecruiters.com/BoschGroup/123"))
        assert result.success
        assert result.method == "smartrecruiters-api"
        assert "Build things" in result.text
        assert "Python" in result.text
        assert result.title == "Werkstudent X"

    def test_detail_404_is_expired(self, monkeypatch):
        def handler(url):
            return _FakeHttpResp(404)

        monkeypatch.setattr(httpx, "AsyncClient", _fake_http_client(handler))
        result = asyncio.run(fetch_jd("https://jobs.smartrecruiters.com/BoschGroup/123"))
        assert result.is_expired
        assert not result.success


class TestFetchJdJsonLdBranch:
    def test_spa_shell_with_jsonld_succeeds(self, monkeypatch):
        shell = "<html><head><title>Job</title></head><body><div id=root></div>"
        ld = (
            '<script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "Werkstudent AI", '
            '"description": "<p>' + ("Real description text. " * 30) + "</p>\"}"
            "</script></body></html>"
        )

        def handler(url):
            return _FakeHttpResp(
                200, text=shell + ld, headers={"content-type": "text/html"}, url=url
            )

        monkeypatch.setattr(httpx, "AsyncClient", _fake_http_client(handler))
        result = asyncio.run(
            fetch_jd("https://example.myworkdayjobs.com/en-US/co/job/1")
        )
        assert result.success
        assert "Real description text" in result.text
        assert result.title == "Werkstudent AI"
