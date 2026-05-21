"""Tests for the JD fetcher LinkedIn extraction."""
import pytest

from utils.jd_fetcher import _extract_linkedin_jd, _clean_html_to_text


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
