"""
Tests for title and location filters.
"""
import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.filters import TitleFilter, LocationFilter, filter_jobs


# ---- TitleFilter tests ----

@pytest.fixture
def title_filter():
    config = {
        "title_filter": {
            "positive": ["Working Student", "Werkstudent", "HiWi", "Student Assistant", "Intern"],
            "negative": ["Senior Engineer", "Director", "Ausbildung", "Vollzeit"],
        }
    }
    return TitleFilter(config)


class TestTitleFilter:
    def test_positive_match(self, title_filter):
        assert title_filter.matches("Working Student in AI") is True

    def test_positive_match_werkstudent(self, title_filter):
        assert title_filter.matches("Werkstudent (m/w/d) - Data Science") is True

    def test_positive_match_hiwi(self, title_filter):
        assert title_filter.matches("HiWi im Bereich Informatik") is True

    def test_negative_match(self, title_filter):
        assert title_filter.matches("Senior Engineer - AI Team") is False

    def test_negative_takes_precedence(self, title_filter):
        """Even if a positive keyword matches, negative should reject."""
        assert title_filter.matches("Working Student Ausbildung Program") is False

    def test_case_insensitive(self, title_filter):
        assert title_filter.matches("werkstudent in marketing") is True
        assert title_filter.matches("WORKING STUDENT") is True

    def test_empty_title_fails(self, title_filter):
        assert title_filter.matches("") is False
        assert title_filter.matches(None) is False

    def test_whitespace_only_fails(self, title_filter):
        assert title_filter.matches("   ") is False

    def test_no_positive_match(self, title_filter):
        assert title_filter.matches("Software Developer Full-time") is False

    def test_empty_positive_list_passes_all(self):
        config = {"title_filter": {"positive": [], "negative": ["Director"]}}
        f = TitleFilter(config)
        assert f.matches("Random Job Title") is True
        assert f.matches("Director of Engineering") is False

    def test_empty_config(self):
        f = TitleFilter({})
        assert f.matches("Any Title") is True


# ---- LocationFilter tests ----

@pytest.fixture
def location_filter():
    config = {
        "location_filter": {
            "allow": ["Erlangen", "Nuremberg", "Munich", "Remote", "Germany"],
            "block": ["Berlin"],
        }
    }
    return LocationFilter(config)


class TestLocationFilter:
    def test_allow_match(self, location_filter):
        assert location_filter.matches("Erlangen, Bavaria, Germany") is True

    def test_allow_nuremberg(self, location_filter):
        assert location_filter.matches("Nuremberg, Bavaria, Germany") is True

    def test_allow_remote(self, location_filter):
        assert location_filter.matches("Remote") is True

    def test_block_takes_precedence(self, location_filter):
        # Berlin is blocked even though Germany is allowed
        assert location_filter.matches("Berlin, Germany") is False

    def test_empty_location_passes(self, location_filter):
        """Don't penalize missing location data."""
        assert location_filter.matches("") is True
        assert location_filter.matches(None) is True

    def test_no_allow_match(self, location_filter):
        assert location_filter.matches("Hamburg") is False

    def test_case_insensitive(self, location_filter):
        assert location_filter.matches("ERLANGEN") is True
        assert location_filter.matches("munich") is True

    def test_disabled_filter(self):
        f = LocationFilter({})
        assert f.matches("Anywhere") is True


# ---- filter_jobs integration ----

class TestFilterJobs:
    def test_full_filter_pipeline(self):
        config = {
            "title_filter": {
                "positive": ["Working Student"],
                "negative": ["Senior"],
            },
            "location_filter": {
                "allow": ["Erlangen"],
                "block": [],
            },
        }
        tf = TitleFilter(config)
        lf = LocationFilter(config)

        jobs = [
            {"title": "Working Student AI", "location": "Erlangen"},
            {"title": "Senior Engineer", "location": "Erlangen"},
            {"title": "Working Student HR", "location": "Berlin"},
            {"title": "Working Student Data", "location": "Erlangen"},
        ]

        result = filter_jobs(jobs, tf, lf)
        assert len(result["passed"]) == 2
        assert len(result["rejected_title"]) == 1
        assert len(result["rejected_location"]) == 1

    def test_empty_jobs_list(self):
        tf = TitleFilter({})
        lf = LocationFilter({})
        result = filter_jobs([], tf, lf)
        assert result["passed"] == []
        assert result["rejected_title"] == []
        assert result["rejected_location"] == []
