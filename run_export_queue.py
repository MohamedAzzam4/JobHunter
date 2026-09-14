"""
run_export_queue.py — Export application_queue.jsonl for UniversalAutoApplier.

Reads the latest evaluations, pipeline.md, and profile.yml, then writes
data/application_queue.jsonl with one ApplicationJob per line.

Usage:
    python run_export_queue.py
    python run_export_queue.py --threshold 4.0
    python run_export_queue.py --output /custom/path.jsonl
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Fix: use a UTF-8 safe stream handler so that job titles containing
# characters like \ufeff (BOM) or other non-cp1252 chars do not crash
# logging on Windows consoles that default to cp1252.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("export_queue")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export application_queue.jsonl for UniversalAutoApplier"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/application_queue.jsonl",
        help="Output JSONL path (default: data/application_queue.jsonl)",
    )
    parser.add_argument(
        "--evaluations",
        type=str,
        default="data/evaluations.json",
        help="Input evaluations.json path",
    )
    parser.add_argument(
        "--pipeline",
        type=str,
        default="data/pipeline.md",
        help="Input pipeline.md path",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="config/profile.yml",
        help="Candidate profile.yml path",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override auto_cv_threshold (default: read from profile.yml)",
    )
    parser.add_argument(
        "--application-id",
        type=str,
        default=None,
        help="Owner-selected: exact application_id to export (requires --owner-selected)",
    )
    parser.add_argument(
        "--owner-selected",
        action="store_true",
        help="Owner-selected pilot mode: permits recommendation 'consider' (never converts to 'apply'), enforces DIRECT_ATS/CAREER_DETAIL_SAFE_APPLY, Siemens/expired/SOURCE_ONLY/lineage gates",
    )
    args = parser.parse_args()

    if args.owner_selected and args.application_id is not None:
        # Normalize application_id: strip, lower, validate 64 hex
        args.application_id = args.application_id.strip().lower()
        if len(args.application_id) != 64 or not all(c in "0123456789abcdef" for c in args.application_id):
            parser.error("--application-id must be a 64-char lowercase hex SHA-256")
    if args.application_id is not None and not args.owner_selected:
        parser.error("--application-id requires --owner-selected (owner authorization)")

    from utils.queue_exporter import export_queue

    summary = export_queue(
        output_path=Path(args.output),
        evaluations_path=Path(args.evaluations),
        pipeline_path=Path(args.pipeline),
        profile_path=Path(args.profile),
        threshold=args.threshold,
        owner_selected=args.owner_selected,
        owner_application_id=args.application_id,
    )

    print("\nExport summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
