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
    args = parser.parse_args()

    from utils.queue_exporter import export_queue

    summary = export_queue(
        output_path=Path(args.output),
        evaluations_path=Path(args.evaluations),
        pipeline_path=Path(args.pipeline),
        profile_path=Path(args.profile),
        threshold=args.threshold,
    )

    print(f"\nExport summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
