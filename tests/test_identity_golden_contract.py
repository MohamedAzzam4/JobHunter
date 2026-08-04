"""Golden identity-contract tests for the queue exporter.

Fixed expected SHA-256 values were derived from UniversalAutoApplier's
AUTHORITATIVE ``core/identity.py`` (spec-loaded standalone during WQ-2, no
runtime import; see the derivation in the WQ-2 handoff). These constants
prove JobHunter's ``canonicalize_url`` / ``_compute_application_id`` and the
exported JSONL rows match UAA's documented identity contract exactly.

Contract (UAA core/identity.py + DATA_CONTRACTS.md):
- sha256(identity_source).hexdigest()
- identity = platform + ":" + external_job_id.strip() ONLY when both are set
  and the id is non-empty after stripping; whitespace-only/empty/None id
  falls back to the canonical URL
- canonical URL: lowercase scheme and host ONLY; userinfo preserved; drop
  fragment + default ports + trailing slash (except host root); strip
  utm_*/tracking keys case-insensitively but keep other keys' original case
  (e.g. jobId); sort remaining pairs by (key, value); re-encode
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
    INVALID_URL,
    _compute_application_id,
    build_queue_entries,
    canonicalize_url,
    export_queue,
)

PROFILE = {"candidate": {"full_name": "Test User", "location": "Erlangen, Germany"}}


# ---------------------------------------------------------------------------
# Golden canonical URLs (from UAA core/identity.py)
# ---------------------------------------------------------------------------

CANONICAL_CASES = [
    # mixed-case scheme and host (path/query case preserved)
    (
        "mixed-case scheme+host",
        "HTTPS://Boards.Greenhouse.IO/Jobs/1?utm_source=linkedin",
        "https://boards.greenhouse.io/Jobs/1",
    ),
    # query ordering
    ("query ordering", "https://x.io/jobs/1?c=3&a=1&b=2", "https://x.io/jobs/1?a=1&b=2&c=3"),
    # tracking-key removal (kept keys keep original case)
    (
        "tracking-key removal",
        "https://x.io/jobs/1?gclid=1&utm_medium=cpc&refid=r&fbclid=f&keep=k",
        "https://x.io/jobs/1?keep=k",
    ),
    # non-tracking mixed-case keys including jobId; case-insensitive tracking
    (
        "mixed-case keys incl jobId",
        "https://x.io/jobs/1?UTM_source=x&jobId=42&Ref=keep&refid=9",
        "https://x.io/jobs/1?jobId=42",
    ),
    # duplicate query keys (both kept, order preserved)
    ("duplicate query keys", "https://x.io/jobs/1?tag=a&tag=b", "https://x.io/jobs/1?tag=a&tag=b"),
    # default ports removed
    ("default http port removed", "http://x.io:80/jobs/1", "http://x.io/jobs/1"),
    ("default https port removed", "https://x.io:443/jobs/1", "https://x.io/jobs/1"),
    # non-default port preserved
    ("non-default port preserved", "https://x.io:8443/jobs/1", "https://x.io:8443/jobs/1"),
    # fragment dropped
    ("fragment dropped", "https://x.io/jobs/1#applicationForm", "https://x.io/jobs/1"),
    # trailing slash behavior
    ("trailing slash removed (non-root)", "https://x.io/jobs/1/", "https://x.io/jobs/1"),
    ("host-root trailing slash kept", "https://x.io/", "https://x.io/"),
    # userinfo preserved exactly
    ("userinfo preserved", "https://user:pass@x.io/jobs/1", "https://user:pass@x.io/jobs/1"),
]


# ---------------------------------------------------------------------------
# Golden SHA-256 application ids (from UAA core/identity.py)
# ---------------------------------------------------------------------------

# sha256("greenhouse:ext-77")
PLATFORM_EXTERNAL_TRIMMED = "28f5e8dd967d0ded18d9134f87172c9b95174c9ad7ef1d3546a751d1a327c4c7"
# sha256(canonical("https://x.io/jobs/2"))
URL_FALLBACK = "655be01fcb15a03bdbdf78daf54a79b95719a678c1abf8b7c2def6fbcfe9d967"
# sha256(canonical("HTTPS://Boards.Greenhouse.IO/Jobs/704?utm_source=linkedin&jobId=42"))
MIXED_CASE_HOST_ID = "4fa6840b3f2ab89dfee52c604951849e41a7038da1d827e060f5cad281927482"
# sha256(canonical("https://user:pass@x.io/jobs/1"))
USERINFO_ID = "1827a247f43f9ba06876965700f408fd4bae47989c8ecd191b73a793514df3dd"
# sha256(canonical("https://x.io:8443/jobs/1?tag=a&tag=b"))
NON_DEFAULT_PORT_ID = "58aabbb0df158f74fd1e5826021d57c4101e47709425fc0f9c0b8357687fccbe"
# sha256("greenhouse:512492") and sha256("greenhouse:0") — integer external
# IDs are normalized to their string form under the UAA lax-str contract.
GOLDEN_INT_512492 = "79fdfd9ff4df4f4721b70b1f86c3fe39b4750de91a28c4c28a6a5eb0201af0aa"
GOLDEN_ZERO = "510564db0eee056738c87fa296a58d28e8999140ece01f70b530a295fc7b9f43"


class TestGoldenCanonicalUrl:
    @pytest.mark.parametrize("name,url,expected", CANONICAL_CASES)
    def test_canonical(self, name: str, url: str, expected: str) -> None:
        assert canonicalize_url(url) == expected, name

    def test_invalid_scheme_rejected(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_url("ftp://x.io/jobs/1")
        with pytest.raises(ValueError):
            canonicalize_url("mailto:someone@example.com")


class TestGoldenApplicationId:
    def test_platform_external_trimmed(self) -> None:
        assert _compute_application_id("greenhouse", "   ext-77  ", "https://x.io/jobs/1") == PLATFORM_EXTERNAL_TRIMMED

    def test_whitespace_only_external_falls_back_to_url(self) -> None:
        assert _compute_application_id("greenhouse", "   ", "https://x.io/jobs/2") == URL_FALLBACK

    def test_empty_external_falls_back_to_url(self) -> None:
        assert _compute_application_id("greenhouse", "", "https://x.io/jobs/2") == URL_FALLBACK

    def test_none_external_falls_back_to_url(self) -> None:
        assert _compute_application_id("greenhouse", None, "https://x.io/jobs/2") == URL_FALLBACK

    def test_mixed_case_host_greenhouse_job(self) -> None:
        assert (
            _compute_application_id(
                None, None, "HTTPS://Boards.Greenhouse.IO/Jobs/704?utm_source=linkedin&jobId=42"
            )
            == MIXED_CASE_HOST_ID
        )

    def test_userinfo_job(self) -> None:
        assert _compute_application_id(None, None, "https://user:pass@x.io/jobs/1") == USERINFO_ID

    def test_non_default_port_job(self) -> None:
        assert _compute_application_id(None, None, "https://x.io:8443/jobs/1?tag=a&tag=b") == NON_DEFAULT_PORT_ID

    def test_non_blank_external_id_skips_url_canonicalization(self) -> None:
        # Mirrors UAA core/identity.py exactly: when a non-blank external id
        # is set, identity_source is platform:external_id and the URL is NOT
        # canonicalized (so no scheme error here). Emitting a row with an
        # invalid URL is prevented separately by build_queue_entries' step-3
        # HTTP(S) guard (proven by the skip tests below), NOT by identity.
        ftp_id = _compute_application_id("greenhouse", "ext-77", "ftp://x.io/jobs/1")
        https_id = _compute_application_id("greenhouse", "ext-77", "https://x.io/jobs/1")
        assert ftp_id == https_id

    def test_integer_job_id_matches_string_contract(self) -> None:
        # An int external id must produce the same SHA-256 as its string form
        # (UAA's lax str coercion turns 512492 into "512492").
        int_id = _compute_application_id("greenhouse", 512492, "https://x.io/jobs/1")
        str_id = _compute_application_id("greenhouse", "512492", "https://x.io/jobs/1")
        assert int_id == GOLDEN_INT_512492
        assert int_id == str_id

    def test_numeric_zero_is_valid_and_deterministic(self) -> None:
        # numeric 0 must NOT be lost to truthiness: it becomes the valid id
        # "0" and never falls back to the URL.
        zero_id = _compute_application_id("greenhouse", 0, "https://x.io/jobs/2")
        str_zero_id = _compute_application_id("greenhouse", "0", "https://x.io/jobs/2")
        assert zero_id == GOLDEN_ZERO
        assert zero_id == str_zero_id
        assert zero_id != URL_FALLBACK  # did NOT degrade to canonical-URL identity

    def test_bool_list_dict_float_never_crash_and_fall_back_to_url(self) -> None:
        # Unsupported external-id values degrade to canonical-URL identity
        # instead of crashing the export.
        for bad in (True, False, ["x"], {"a": 1}, 3.14):
            assert _compute_application_id("greenhouse", bad, "https://x.io/jobs/2") == URL_FALLBACK


class TestGoldenContractInExportedRow:
    """The produced JSONL row must carry exactly the golden application_id."""

    def test_exported_row_id_matches_golden(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")

        evaluation = {
            "success": True,
            "url": "HTTPS://Boards.Greenhouse.IO/Jobs/704?utm_source=linkedin&jobId=42",
            "company": "Acme",
            "title": "Engineer",
            "global_score": 4.5,
            "recommendation": "apply",
            "cv_pdf_path": str(cv),
            "cover_letter_pdf_path": str(cover),
            "description": "Do the things.",
        }
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps([evaluation]), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
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
        assert rows[0]["application_id"] == MIXED_CASE_HOST_ID
        # Recomputed exactly like UAA ApplicationJob._validate_application_id.
        assert rows[0]["application_id"] == _compute_application_id(
            rows[0]["platform"], rows[0].get("external_job_id"), rows[0]["url"]
        )

    def test_invalid_scheme_row_is_skipped_not_emitted(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")

        evals = [
            {
                "success": True,
                "url": "ftp://x.io/jobs/1",
                "company": "Acme",
                "title": "Engineer",
                "global_score": 4.5,
                "recommendation": "apply",
                "cv_pdf_path": str(cv),
                "cover_letter_pdf_path": str(cover),
                "external_job_id": "ext-77",
            }
        ]
        rows, skipped = build_queue_entries(evals, {}, {}, threshold=3.5)
        assert rows == []
        assert [r["reason"] for r in skipped] == [INVALID_URL]

    @staticmethod
    def _export_single(tmp_path: Path, evaluation: dict) -> list[dict]:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")
        evaluation.setdefault("success", True)
        evaluation.setdefault("company", "Acme")
        evaluation.setdefault("title", "Engineer")
        evaluation.setdefault("global_score", 4.5)
        evaluation.setdefault("recommendation", "apply")
        evaluation.setdefault("cv_pdf_path", str(cv))
        evaluation.setdefault("cover_letter_pdf_path", str(cover))
        evals_path = tmp_path / "evaluations.json"
        evals_path.write_text(json.dumps([evaluation]), encoding="utf-8")
        profile_path = tmp_path / "profile.yml"
        profile_path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
        output_path = tmp_path / "application_queue.jsonl"
        export_queue(
            output_path=output_path,
            evaluations_path=evals_path,
            pipeline_path=tmp_path / "nope.md",
            profile_path=profile_path,
            threshold=3.5,
        )
        return [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    def test_exported_int_external_id_normalized_to_string(self, tmp_path: Path) -> None:
        rows = self._export_single(
            tmp_path, {"url": "https://boards.greenhouse.io/example/jobs/1001", "external_job_id": 512492}
        )
        assert len(rows) == 1
        row = rows[0]
        # Emitted field is the normalized string, never the integer.
        assert row["external_job_id"] == "512492"
        assert row["application_id"] == GOLDEN_INT_512492  # sha256("greenhouse:512492")

    def test_exported_numeric_zero_external_id_valid(self, tmp_path: Path) -> None:
        rows = self._export_single(
            tmp_path, {"url": "https://boards.greenhouse.io/example/jobs/1003", "external_job_id": 0}
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["external_job_id"] == "0"
        assert row["application_id"] == GOLDEN_ZERO  # sha256("greenhouse:0")

    def test_exported_bool_external_id_is_null_and_url_identity(self, tmp_path: Path) -> None:
        url = "https://boards.greenhouse.io/example/jobs/1002"
        rows = self._export_single(tmp_path, {"url": url, "external_job_id": True})
        assert len(rows) == 1
        row = rows[0]
        # Unsupported types are absent in the emitted JSONL (null, never bool)
        # and the identity degrades to the canonical URL.
        assert row["external_job_id"] is None
        assert row["application_id"] == _compute_application_id(None, None, url)