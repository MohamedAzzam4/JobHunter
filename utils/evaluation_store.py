"""JSON evaluation persistence.

Appends each evaluation record to ``data/evaluations.json`` so that
``run_export_queue.py`` can read a structured, machine-readable record
of every evaluation (the Excel file is for human review; this JSON
file is the canonical input for the UAA queue exporter).

The file is a JSON array of evaluation dicts. Each call to
:func:`append_evaluation` appends one record. The record includes:
- The full evaluation dict (score, recommendation, german_level, etc.)
- The job URL, company, title, description, date_posted
- The cv_pdf_path and cover_letter_pdf_path if generated
- A timestamp

The file is loaded, appended to, and rewritten atomically (write to
temp, then rename) so that a crash mid-write does not corrupt the file.

This module is intentionally robust: if the file does not exist, it is
created. If it exists but is corrupt JSON, it is reset. If the
evaluation dict is not serializable, the offending values are stringified.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_output_path() -> Path:
    return Path("data/evaluations.json")


def _safe_serialize(obj: Any) -> Any:
    """Make ``obj`` JSON-serializable by converting non-serializable
    values to their string representation."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Fallback: stringify anything else.
    return str(obj)


def append_evaluation(
    evaluation: dict[str, Any],
    output_path: Path | str | None = None,
) -> Path:
    """Append one evaluation record to the JSON evaluations file.

    Args:
        evaluation: The evaluation dict produced by ``Evaluator.evaluate()``
            and enriched by ``evaluate_job()`` (with cv_pdf_path,
            cover_letter_pdf_path, etc.).
        output_path: Path to the JSON file. Defaults to
            ``data/evaluations.json``.

    Returns:
        The path that was written to.
    """
    path = Path(output_path) if output_path else _default_output_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing records (or start fresh if the file is missing/corrupt).
    records: list[dict[str, Any]] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list):
                records = existing
            else:
                logger.warning(
                    "%s contains a JSON object, not an array; resetting",
                    path,
                )
        except json.JSONDecodeError as exc:
            logger.warning("%s is corrupt (%s); resetting", path, exc)

    # Build the record to append. Add a timestamp.
    record: dict[str, Any] = _safe_serialize(evaluation)
    record["appended_at"] = datetime.now(timezone.utc).isoformat()

    # Deduplicate by URL: if a record with the same URL exists, replace it.
    # This makes the file idempotent — re-evaluating the same job updates
    # its record rather than appending a duplicate.
    url = record.get("url", "")
    if url:
        records = [r for r in records if r.get("url") != url]
    records.append(record)

    # Atomic write: write to temp, then rename.
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    tmp_path.replace(path)

    logger.info("evaluation record appended to %s (total: %d)", path, len(records))
    return path


def load_evaluations(output_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Load all evaluation records from the JSON file.

    Args:
        output_path: Path to the JSON file. Defaults to
            ``data/evaluations.json``.

    Returns:
        A list of evaluation dicts. Returns an empty list if the file
        does not exist or is corrupt.
    """
    path = Path(output_path) if output_path else _default_output_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("%s contains a JSON object, not an array", path)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("%s is corrupt (%s)", path, exc)
        return []


__all__ = ["append_evaluation", "load_evaluations"]
