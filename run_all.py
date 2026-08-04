"""
run_all.py — Full pipeline: scan → evaluate → generate.

Runs the complete job search pipeline end-to-end.
Designed for cron/scheduled execution on a server.

Usage:
    python run_all.py              Full pipeline
    python run_all.py --dry-run    Scan only, don't evaluate
    python run_all.py --scan-only  Scan without evaluation
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from run_scan import run_scan
from run_evaluate import run_evaluate

from utils.queue_exporter import export_queue
from utils.utf8_logging import get_utf8_stream_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[get_utf8_stream_handler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)
file_handler = logging.FileHandler(logs_dir / "pipeline.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.getLogger().addHandler(file_handler)


async def run_pipeline(dry_run: bool = False, scan_only: bool = False, threshold_override: float | None = None, german_policy: str | None = None):
    """Execute the full pipeline."""
    load_dotenv()

    # Load threshold from config (or CLI override)
    profile_path = Path("config/profile.yml")
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
    else:
        profile = {}
    threshold = threshold_override if threshold_override is not None else profile.get("evaluation", {}).get("auto_cv_threshold", 3.5)
    start = datetime.now()

    logger.info(">>> " + "=" * 56)
    logger.info(f"   JOB SEARCH PIPELINE — {start.strftime('%Y-%m-%d %H:%M')}")
    logger.info("   " + "=" * 56)

    # Phase 1: Scan
    logger.info("\n[SCAN] PHASE 1: Scanning for new jobs...")
    scan_summary = await run_scan(dry_run=dry_run)

    if dry_run:
        logger.info("Dry run complete. No further action.")
        return

    if scan_only:
        logger.info("Scan-only mode. Skipping evaluation.")
        return

    new_jobs = scan_summary.get("new_added", 0)
    if new_jobs == 0:
        logger.info("No new jobs found. Pipeline complete.")
        return

    # Phase 2: Evaluate new jobs
    logger.info(f"\n[EVAL] PHASE 2: Evaluating {new_jobs} new job(s)...")
    eval_results = await run_evaluate(mode="all", threshold_override=threshold_override, german_policy=german_policy)

    # Phase 2.5: Publish the application queue for UniversalAutoApplier.
    # Runs only after scan + evaluate succeeded. A failed export fails the
    # pipeline (never leave a silently stale queue behind).
    logger.info("\n[EXPORT] PHASE 2.5: Publishing application_queue.jsonl...")
    export_summary = export_queue(threshold=threshold)
    logger.info(
        "[EXPORT] Published %d job(s) to %s (skipped %d)",
        export_summary["exported"],
        export_summary["output_path"],
        export_summary["skipped"],
    )
    skipped_reasons = export_summary.get("skipped_reasons", {})
    if skipped_reasons:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(skipped_reasons.items()))
        logger.info("[EXPORT] Skip reasons: %s", reasons)

    # Phase 3: Summary
    elapsed = (datetime.now() - start).total_seconds()
    successful_evals = [r for r in eval_results if r.get("success")]
    high_scores = [r for r in successful_evals if r.get("global_score", 0) >= threshold]

    logger.info(f"\n{'='*60}")
    logger.info("PIPELINE COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"  Duration:         {elapsed:.0f}s")
    logger.info(f"  Jobs scanned:     {scan_summary.get('total_found', 0)}")
    logger.info(f"  New jobs:         {new_jobs}")
    logger.info(f"  Evaluated:        {len(successful_evals)}")
    logger.info(f"  Score >= {threshold}:     {len(high_scores)} (CV + cover letter generated)")
    logger.info(f"  Queue exported:   {export_summary['exported']} job(s) -> {export_summary['output_path']}")

    if high_scores:
        logger.info("\nTop matches:")
        for r in sorted(high_scores, key=lambda x: x.get("global_score", 0), reverse=True):
            logger.info(
                f"  * {r.get('global_score', '?')}/5 -- "
                f"{r.get('company', '?')} — {r.get('title', '?')}"
            )


def main():
    parser = argparse.ArgumentParser(description="Full job search pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, don't write")
    parser.add_argument("--scan-only", action="store_true", help="Scan without evaluating")
    parser.add_argument("--threshold", type=float, default=None, help="Override auto_cv_threshold")
    parser.add_argument("--german-policy", type=str, choices=["reject_b1_plus", "reject_b2_plus_only", "reject_unless_bilingual", "accept_all"], default=None, help="Override German filter policy")
    args = parser.parse_args()

    asyncio.run(run_pipeline(dry_run=args.dry_run, scan_only=args.scan_only, threshold_override=args.threshold, german_policy=args.german_policy))


if __name__ == "__main__":
    main()
