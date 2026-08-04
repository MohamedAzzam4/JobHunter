"""Contract conformance test: exported queue rows must satisfy
UniversalAutoApplier's ApplicationJob JSONL contract.

Source of truth (read-only, in the UAA repository):
- docs/generalization/DATA_CONTRACTS.md
- src/universal_auto_applier/core/models.py   (ApplicationJob)
- src/universal_auto_applier/core/identity.py (compute_application_id)
- src/universal_auto_applier/application_queue/importer.py

The canonical URL and application_id rules are re-implemented HERE
(independently of the exporter) so that drift in either implementation is
caught by this test. Local fixtures only — no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.queue_exporter import export_queue  # noqa: E402

REQUIRED_FIELDS = [
    "application_id",
    "platform",
    "source",
    "company",
    "title",
    "url",
    "location",
    "job_description",
    "score",
    "verdict",
    "cv_pdf",
    "cover_letter_pdf",
    "status",
]
VERDICTS = {"apply", "consider", "skip"}
VALID_SCHEMES = {"http", "https"}

PROFILE = {"candidate": {"full_name": "Test User", "location": "Erlangen, Germany"}}


def uaa_canonical_url(url: str) -> str:
    """Mirror UAA core/identity.py canonicalization: lowercase scheme+host,
    strip fragment + default ports + trailing slash (except host root),
    drop utm_* / tracking query params (case-insensitive), sort the rest."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    drop_prefixes = ("utm_",)
    drop_keys = {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid"}
    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        kl = k.lower()
        if any(kl.startswith(p) for p in drop_prefixes):
            continue
        if kl in drop_keys:
            continue
        kept.append((kl, v))
    kept.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(kept)
    out = f"{scheme}://{netloc}{path}"
    if query:
        out += f"?{query}"
    return out


def uaa_application_id(platform: str, external_job_id: str | None, url: str) -> str:
    """Mirror UAA identity computation: sha256(platform:external_id) else
    sha256(canonical URL)."""
    if platform and external_job_id:
        identity = f"{platform}:{external_job_id.strip()}"
    else:
        identity = uaa_canonical_url(url)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@pytest.fixture
def exported_rows(tmp_path: Path) -> list[dict]:
    cv1 = tmp_path / "cv1.pdf"
    cover1 = tmp_path / "cover1.pdf"
    cv2 = tmp_path / "cv2.pdf"
    cover2 = tmp_path / "cover2.pdf"
    for p in (cv1, cover1, cv2, cover2):
        p.write_bytes(b"%PDF-1.4 fake")

    evals = [
        # Greenhouse job with tracking params on the URL (no external id).
        {
            "success": True,
            "url": "https://boards.greenhouse.io/acme/jobs/704?utm_source=linkedin&refid=99&page=3",
            "company": "Acme",
            "title": "Engineer",
            "global_score": 4.5,
            "recommendation": "apply",
            "cv_pdf_path": str(cv1),
            "cover_letter_pdf_path": str(cover1),
            "description": "Build things.",
            "location": "Berlin, Germany",
        },
        # Lever job with an external id -> platform:external_id identity.
        {
            "success": True,
            "url": "https://jobs.lever.co/techco/12345",
            "company": "TechCo",
            "title": "PM",
            "global_score": 3.9,
            "recommendation": "apply",
            "cv_pdf_path": str(cv2),
            "cover_letter_pdf_path": str(cover2),
            "description": "Manage things.",
            "external_job_id": "lever-12345",
        },
    ]
    evals_path = tmp_path / "evaluations.json"
    evals_path.write_text(json.dumps(evals), encoding="utf-8")
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

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


class TestUaaContract:
    def test_all_rows_have_required_fields(self, exported_rows) -> None:
        assert len(exported_rows) == 2
        for row in exported_rows:
            for field in REQUIRED_FIELDS:
                assert field in row, f"missing required field {field!r}"
                assert row[field] != "" or field in ("location", "job_description"), (
                    f"required field {field!r} is empty: {row[field]!r}"
                )

    def test_application_id_matches_uaa_recompute(self, exported_rows) -> None:
        for row in exported_rows:
            recomputed = uaa_application_id(
                row["platform"], row.get("external_job_id"), row["url"]
            )
            assert row["application_id"] == recomputed

    def test_application_ids_unique(self, exported_rows) -> None:
        ids = [r["application_id"] for r in exported_rows]
        assert len(ids) == len(set(ids))

    def test_url_is_http_with_hostname(self, exported_rows) -> None:
        for row in exported_rows:
            parts = urlsplit(row["url"])
            assert parts.scheme.lower() in VALID_SCHEMES
            assert parts.hostname  # non-empty hostname required

    def test_verdict_is_valid_enum(self, exported_rows) -> None:
        for row in exported_rows:
            assert row["verdict"] in VERDICTS

    def test_ready_to_apply_requires_existing_absolute_pdfs(self, exported_rows) -> None:
        for row in exported_rows:
            if row["status"] != "ready_to_apply":
                continue
            for key in ("cv_pdf", "cover_letter_pdf"):
                value = row[key]
                assert Path(value).is_absolute(), f"{key} must be absolute, got {value!r}"
                assert Path(value).exists(), f"{key} must exist on disk, got {value!r}"

    def test_canonicalization_includes_non_tracking_params(self) -> None:
        # refid (tracking) and utm_* are dropped; page=3 is kept and sorted.
        a = uaa_canonical_url(
            "https://boards.greenhouse.io/acme/jobs/1?utm_source=x&refid=9&page=3"
        )
        b = uaa_canonical_url("https://boards.greenhouse.io/acme/jobs/1?page=3")
        assert a == b

    def test_uexport_greenhouse_external_id_empty_falls_back_to_url(self) -> None:
        # Same as exporter: empty external id must behave like no external id.
        assert uaa_application_id("greenhouse", "", "https://x.io/jobs/1") == uaa_application_id(
            "greenhouse", None, "https://x.io/jobs/1"
        )