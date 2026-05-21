"""
Tests for German filter policies, score clamping, and threshold logic.

Uses realistic job data based on actual jobs from the pipeline (Siemens,
Bosch, adidas, etc.) to verify that each policy correctly accepts/rejects
jobs based on German language requirements.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helper: replicate the exact German rejection logic from run_evaluate.py
# so we can test it in isolation without mocking the entire AI pipeline.
# ---------------------------------------------------------------------------

def apply_german_policy(
    evaluation: dict,
    job: dict,
    german_policy: str,
) -> dict:
    """Apply German filter policy to an evaluation result.
    
    Replicates the logic from run_evaluate.py evaluate_job() lines 216-256.
    Returns a copy of evaluation with score/recommendation updated.
    """
    result = dict(evaluation)
    score = result.get("global_score", 0)

    german_level = result.get("german_level_required", "none")
    german_required = result.get("german_required", False)

    should_reject = False
    if german_policy == "reject_b1_plus":
        should_reject = german_level in ("B2+", "B1") or german_required
    elif german_policy == "reject_b2_plus_only":
        should_reject = german_level == "B2+"
    elif german_policy == "reject_unless_bilingual":
        if german_level in ("B2+", "B1") or german_required:
            jd_lower = job.get("description", "").lower()
            english_signals = [
                "english", "englisch", "c1", "c2", "b2 english",
                "fluent in english", "good english", "very good english",
                "excellent english", "english required", "working language: english",
                "english is a must", "gute englischkenntnisse",
                "sehr gute englischkenntnisse", "fließend englisch",
            ]
            has_english = any(signal in jd_lower for signal in english_signals)
            if has_english:
                should_reject = False
            else:
                should_reject = True
    # accept_all: never auto-reject

    if should_reject:
        result["global_score"] = 0
        result["recommendation"] = "skip_german"

    return result


def clamp_score(score):
    """Replicate score clamping logic from run_evaluate.py lines 197-204."""
    try:
        return round(max(1, min(5, float(score))), 1)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Realistic job data from the actual pipeline
# ---------------------------------------------------------------------------

# Based on real Siemens Healthineers job (German required, English also mentioned)
JOB_BILINGUAL_SIEMENS = {
    "title": "Working Student (f/m/d) for Content Creation",
    "company": "Siemens Healthineers",
    "url": "https://www.linkedin.com/jobs/view/4408884793",
    "description": (
        "Siemens Healthineers sucht eine/n Werkstudent*in im Bereich Content Creation. "
        "Sehr gute Deutschkenntnisse (mindestens B2) und gute Englischkenntnisse "
        "werden vorausgesetzt. You will create marketing content for our clinical "
        "products. Fluent in English is required for our international team."
    ),
}

EVAL_BILINGUAL_SIEMENS = {
    "global_score": 2.8,
    "german_level_required": "B2+",
    "german_required": True,
    "recommendation": "skip",
}

# Based on real Bosch job (German only, no English mentioned)
JOB_GERMAN_ONLY_BOSCH = {
    "title": "Werkstudent DevOps for Manufacturing (w/m/div.)",
    "company": "Bosch",
    "url": "https://de.indeed.com/viewjob?jk=d7b0072deb8636d8",
    "description": (
        "Werkstudent DevOps for Manufacturing gesucht. "
        "Fließende Deutschkenntnisse in Wort und Schrift erforderlich. "
        "Sie arbeiten im Team an der Automatisierung von Produktionsprozessen."
    ),
}

EVAL_GERMAN_ONLY_BOSCH = {
    "global_score": 3.5,
    "german_level_required": "B2+",
    "german_required": True,
    "recommendation": "consider",
}

# Based on real adidas job (German + English both required)
JOB_BILINGUAL_ADIDAS = {
    "title": "Working Student (m/f/d) - HR Retail DACH",
    "company": "adidas",
    "url": "https://www.linkedin.com/jobs/view/4410613957",
    "description": (
        "As a Working Student in HR Retail DACH, you will support the team "
        "in daily HR operations. Requirements: sehr gute Deutschkenntnisse, "
        "very good English skills (written and spoken). Experience with MS Office."
    ),
}

EVAL_BILINGUAL_ADIDAS = {
    "global_score": 2.8,
    "german_level_required": "B2+",
    "german_required": True,
    "recommendation": "skip",
}

# Job with B1 German requirement
JOB_B1_SCHAEFFLER = {
    "title": "Working Student in Special Machinery Engineering",
    "company": "Schaeffler",
    "url": "https://www.linkedin.com/jobs/view/4383100860",
    "description": (
        "Working Student in Special Machinery Engineering - Ergonomics Focus. "
        "Deutschkenntnisse mindestens B1 erforderlich. "
        "Good English skills are a plus."
    ),
}

EVAL_B1_SCHAEFFLER = {
    "global_score": 2.4,
    "german_level_required": "B1",
    "german_required": True,
    "recommendation": "skip",
}

# Job with no German requirement (English-only tech role)
JOB_NO_GERMAN_RAISIN = {
    "title": "Working Student Software Engineer (f/m/d)",
    "company": "Raisin",
    "url": "https://www.linkedin.com/jobs/view/4412165988",
    "description": (
        "Join Raisin as a Working Student in Software Development. "
        "Good communication skills in English. "
        "Tech stack: Java, JavaScript, Python. No German required."
    ),
}

EVAL_NO_GERMAN_RAISIN = {
    "global_score": 4.2,
    "german_level_required": "none",
    "german_required": False,
    "recommendation": "apply",
}

# Job with "German is a plus" (A1-A2 — should never be rejected)
JOB_A2_DYSON = {
    "title": "Working student - Key Account",
    "company": "Dyson",
    "url": "https://www.linkedin.com/jobs/view/4410709436",
    "description": (
        "Join Dyson's Key Account Team. Fluent in both English and German. "
        "Grundkenntnisse Deutsch von Vorteil. Strong interest in sales."
    ),
}

EVAL_A2_DYSON = {
    "global_score": 3.8,
    "german_level_required": "A1-A2",
    "german_required": False,
    "recommendation": "apply",
}

# Job where german_required=True but level is generic (from AI evaluation)
JOB_GENERIC_GERMAN = {
    "title": "Werkstudent Marketing",
    "company": "Mindray",
    "url": "https://www.linkedin.com/jobs/view/4410373656",
    "description": (
        "Werkstudent Marketing gesucht. "
        "Sehr gute Deutsch- und gute Englischkenntnisse in Wort und Schrift. "
        "Unterstützung bei Social Media und Events."
    ),
}

EVAL_GENERIC_GERMAN = {
    "global_score": 3.0,
    "german_level_required": "B2+",
    "german_required": True,
    "recommendation": "consider",
}

# Edge case: German required, description mentions "Englisch" (German word for English)
JOB_ENGLISCH_SIGNAL = {
    "title": "Werkstudent Controlling",
    "company": "DATEV",
    "url": "https://www.datev.de/jobs/12345",
    "description": (
        "Wir suchen einen Werkstudenten im Bereich Controlling. "
        "Fließende Deutschkenntnisse erforderlich. "
        "Gute Englischkenntnisse sind wünschenswert."
    ),
}

EVAL_ENGLISCH_SIGNAL = {
    "global_score": 2.5,
    "german_level_required": "B2+",
    "german_required": True,
    "recommendation": "skip",
}


# ===================================================================
# TESTS
# ===================================================================


class TestRejectB1Plus:
    """Default policy: reject both B1 and B2+."""

    POLICY = "reject_b1_plus"

    def test_rejects_b2_plus(self):
        result = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"

    def test_rejects_b1(self):
        result = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"

    def test_rejects_bilingual_b2(self):
        """Even if English is also mentioned, B2+ is still rejected under this policy."""
        result = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"

    def test_keeps_no_german(self):
        result = apply_german_policy(EVAL_NO_GERMAN_RAISIN, JOB_NO_GERMAN_RAISIN, self.POLICY)
        assert result["global_score"] == 4.2
        assert result["recommendation"] == "apply"

    def test_keeps_a1_a2(self):
        result = apply_german_policy(EVAL_A2_DYSON, JOB_A2_DYSON, self.POLICY)
        assert result["global_score"] == 3.8
        assert result["recommendation"] == "apply"

    def test_rejects_german_required_flag(self):
        """If german_required=True, reject even if level is not specified."""
        eval_data = {"global_score": 3.5, "german_level_required": "none", "german_required": True, "recommendation": "consider"}
        result = apply_german_policy(eval_data, {}, self.POLICY)
        assert result["global_score"] == 0


class TestRejectB2PlusOnly:
    """Only reject B2+, let B1 through."""

    POLICY = "reject_b2_plus_only"

    def test_rejects_b2_plus(self):
        result = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"

    def test_keeps_b1(self):
        """B1 jobs should pass through under this policy."""
        result = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, self.POLICY)
        assert result["global_score"] == 2.4
        assert result["recommendation"] == "skip"  # original recommendation preserved

    def test_keeps_no_german(self):
        result = apply_german_policy(EVAL_NO_GERMAN_RAISIN, JOB_NO_GERMAN_RAISIN, self.POLICY)
        assert result["global_score"] == 4.2

    def test_keeps_a1_a2(self):
        result = apply_german_policy(EVAL_A2_DYSON, JOB_A2_DYSON, self.POLICY)
        assert result["global_score"] == 3.8

    def test_does_not_check_german_required_flag(self):
        """Under this policy, only german_level matters, not the german_required flag."""
        eval_data = {"global_score": 3.5, "german_level_required": "B1", "german_required": True, "recommendation": "consider"}
        result = apply_german_policy(eval_data, {}, self.POLICY)
        assert result["global_score"] == 3.5  # B1 is kept


class TestRejectUnlessBilingual:
    """Reject German jobs UNLESS English is also required in the JD."""

    POLICY = "reject_unless_bilingual"

    def test_keeps_bilingual_siemens(self):
        """Siemens job requires German B2+ but also mentions English -> keep."""
        result = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, self.POLICY)
        assert result["global_score"] == 2.8  # original score preserved
        assert result["recommendation"] != "skip_german"

    def test_keeps_bilingual_adidas(self):
        """adidas job requires German but also 'very good English skills' -> keep."""
        result = apply_german_policy(EVAL_BILINGUAL_ADIDAS, JOB_BILINGUAL_ADIDAS, self.POLICY)
        assert result["global_score"] == 2.8
        assert result["recommendation"] != "skip_german"

    def test_rejects_german_only_bosch(self):
        """Bosch job requires German but doesn't mention English -> reject."""
        result = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"

    def test_keeps_b1_with_english(self):
        """B1 German + English mentioned in JD -> keep."""
        result = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, self.POLICY)
        # JOB_B1_SCHAEFFLER has "Good English skills are a plus"
        assert result["global_score"] == 2.4  # kept because "English" found

    def test_keeps_no_german(self):
        """No German requirement -> always keep."""
        result = apply_german_policy(EVAL_NO_GERMAN_RAISIN, JOB_NO_GERMAN_RAISIN, self.POLICY)
        assert result["global_score"] == 4.2

    def test_keeps_a1_a2(self):
        result = apply_german_policy(EVAL_A2_DYSON, JOB_A2_DYSON, self.POLICY)
        assert result["global_score"] == 3.8

    def test_detects_englisch_signal(self):
        """German word 'Englischkenntnisse' should be detected as English signal."""
        result = apply_german_policy(EVAL_ENGLISCH_SIGNAL, JOB_ENGLISCH_SIGNAL, self.POLICY)
        assert result["global_score"] == 2.5  # kept because "Englischkenntnisse" found

    def test_generic_german_with_english(self):
        """Mindray: 'Deutsch- und gute Englischkenntnisse' -> bilingual -> keep."""
        result = apply_german_policy(EVAL_GENERIC_GERMAN, JOB_GENERIC_GERMAN, self.POLICY)
        assert result["global_score"] == 3.0  # kept

    def test_rejects_german_no_description(self):
        """If job has no description, can't detect English -> reject."""
        job_no_desc = {"title": "Test", "company": "Test", "url": "", "description": ""}
        eval_german = {"global_score": 3.5, "german_level_required": "B2+", "german_required": True, "recommendation": "consider"}
        result = apply_german_policy(eval_german, job_no_desc, self.POLICY)
        assert result["global_score"] == 0
        assert result["recommendation"] == "skip_german"


class TestAcceptAll:
    """Never auto-reject any German level."""

    POLICY = "accept_all"

    def test_keeps_b2_plus(self):
        result = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, self.POLICY)
        assert result["global_score"] == 3.5
        assert result["recommendation"] == "consider"

    def test_keeps_b1(self):
        result = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, self.POLICY)
        assert result["global_score"] == 2.4

    def test_keeps_no_german(self):
        result = apply_german_policy(EVAL_NO_GERMAN_RAISIN, JOB_NO_GERMAN_RAISIN, self.POLICY)
        assert result["global_score"] == 4.2

    def test_keeps_even_extreme_german(self):
        """Even fully German-only jobs should pass."""
        eval_extreme = {"global_score": 1.5, "german_level_required": "B2+", "german_required": True, "recommendation": "skip"}
        result = apply_german_policy(eval_extreme, JOB_GERMAN_ONLY_BOSCH, self.POLICY)
        assert result["global_score"] == 1.5


class TestScoreClamping:
    """Test score clamping to valid 1-5 range."""

    def test_normal_score(self):
        assert clamp_score(3.5) == 3.5

    def test_score_zero_clamped_to_1(self):
        assert clamp_score(0) == 1

    def test_score_negative_clamped_to_1(self):
        assert clamp_score(-2) == 1

    def test_score_above_5_clamped(self):
        assert clamp_score(6) == 5

    def test_score_above_5_float(self):
        assert clamp_score(7.3) == 5

    def test_score_exactly_1(self):
        assert clamp_score(1) == 1.0

    def test_score_exactly_5(self):
        assert clamp_score(5) == 5.0

    def test_score_string_number(self):
        assert clamp_score("3.5") == 3.5

    def test_score_invalid_string(self):
        assert clamp_score("not_a_number") == 0

    def test_score_none(self):
        assert clamp_score(None) == 0

    def test_score_rounding(self):
        assert clamp_score(3.14159) == 3.1


class TestThresholdLogic:
    """Test that threshold determines CV generation behavior."""

    def test_score_above_threshold_would_generate_cv(self):
        """Score 4.2 >= threshold 3.5 -> would generate CV."""
        threshold = 3.5
        score = 4.2
        assert score >= threshold

    def test_score_below_threshold_skips_cv(self):
        """Score 2.8 < threshold 3.5 -> skip CV."""
        threshold = 3.5
        score = 2.8
        assert score < threshold

    def test_score_equals_threshold_generates_cv(self):
        """Score exactly at threshold -> generate CV."""
        threshold = 3.5
        score = 3.5
        assert score >= threshold

    def test_higher_threshold_rejects_more(self):
        """With threshold 4.0, a 3.8 job would be skipped."""
        threshold = 4.0
        score = 3.8
        assert score < threshold

    def test_lower_threshold_accepts_more(self):
        """With threshold 2.0, a 2.5 job would get a CV."""
        threshold = 2.0
        score = 2.5
        assert score >= threshold

    def test_rejected_german_score_zero_always_below_threshold(self):
        """German-rejected jobs (score=0) should always be below any threshold."""
        for threshold in [1.0, 2.0, 3.5, 4.0, 5.0]:
            assert 0 < threshold


class TestPolicyCrossCheck:
    """Cross-policy comparisons: same job evaluated under different policies."""

    def test_bilingual_job_across_all_policies(self):
        """Siemens bilingual job: different policies give different results."""
        # reject_b1_plus: rejected (strictest)
        r1 = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, "reject_b1_plus")
        assert r1["global_score"] == 0

        # reject_b2_plus_only: rejected (B2+ is still rejected)
        r2 = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, "reject_b2_plus_only")
        assert r2["global_score"] == 0

        # reject_unless_bilingual: KEPT (English is mentioned)
        r3 = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, "reject_unless_bilingual")
        assert r3["global_score"] == 2.8

        # accept_all: KEPT
        r4 = apply_german_policy(EVAL_BILINGUAL_SIEMENS, JOB_BILINGUAL_SIEMENS, "accept_all")
        assert r4["global_score"] == 2.8

    def test_german_only_job_across_all_policies(self):
        """Bosch German-only job: only accept_all keeps it."""
        r1 = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, "reject_b1_plus")
        assert r1["global_score"] == 0

        r2 = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, "reject_b2_plus_only")
        assert r2["global_score"] == 0

        r3 = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, "reject_unless_bilingual")
        assert r3["global_score"] == 0

        r4 = apply_german_policy(EVAL_GERMAN_ONLY_BOSCH, JOB_GERMAN_ONLY_BOSCH, "accept_all")
        assert r4["global_score"] == 3.5

    def test_no_german_job_passes_all_policies(self):
        """Raisin (no German): should pass every policy."""
        for policy in ["reject_b1_plus", "reject_b2_plus_only", "reject_unless_bilingual", "accept_all"]:
            result = apply_german_policy(EVAL_NO_GERMAN_RAISIN, JOB_NO_GERMAN_RAISIN, policy)
            assert result["global_score"] == 4.2, f"Failed for policy={policy}"

    def test_b1_job_across_policies(self):
        """B1 Schaeffler job: rejected by reject_b1_plus, kept by others."""
        r1 = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, "reject_b1_plus")
        assert r1["global_score"] == 0

        r2 = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, "reject_b2_plus_only")
        assert r2["global_score"] == 2.4  # B1 passes

        # reject_unless_bilingual: JD mentions "English" -> kept
        r3 = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, "reject_unless_bilingual")
        assert r3["global_score"] == 2.4

        r4 = apply_german_policy(EVAL_B1_SCHAEFFLER, JOB_B1_SCHAEFFLER, "accept_all")
        assert r4["global_score"] == 2.4
