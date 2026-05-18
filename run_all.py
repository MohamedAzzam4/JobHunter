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

from dotenv import load_dotenv

from run_scan import run_scan
from run_evaluate import run_evaluate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)
file_handler = logging.FileHandler(logs_dir / "pipeline.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.getLogger().addHandler(file_handler)


async def run_pipeline(dry_run: bool = False, scan_only: bool = False):
    """Execute the full pipeline."""
    load_dotenv()
    start = datetime.now()

    logger.info("🚀 " + "=" * 56)
    logger.info(f"   JOB SEARCH PIPELINE — {start.strftime('%Y-%m-%d %H:%M')}")
    logger.info("   " + "=" * 56)

    # Phase 1: Scan
    logger.info("\n📡 PHASE 1: Scanning for new jobs...")
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
    logger.info(f"\n🤖 PHASE 2: Evaluating {new_jobs} new job(s)...")
    eval_results = await run_evaluate(mode="all")

    # Phase 3: Summary
    elapsed = (datetime.now() - start).total_seconds()
    successful_evals = [r for r in eval_results if r.get("success")]
    high_scores = [r for r in successful_evals if r.get("global_score", 0) >= 3.5]

    logger.info(f"\n{'='*60}")
    logger.info("📊 PIPELINE COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"  Duration:         {elapsed:.0f}s")
    logger.info(f"  Jobs scanned:     {scan_summary.get('total_found', 0)}")
    logger.info(f"  New jobs:         {new_jobs}")
    logger.info(f"  Evaluated:        {len(successful_evals)}")
    logger.info(f"  Score >= 3.5:     {len(high_scores)} (CV + cover letter generated)")

    if high_scores:
        logger.info("\n🎯 Top matches:")
        for r in sorted(high_scores, key=lambda x: x.get("global_score", 0), reverse=True):
            logger.info(
                f"  ⭐ {r.get('global_score', '?')}/5 — "
                f"{r.get('company', '?')} — {r.get('title', '?')}"
            )


def main():
    parser = argparse.ArgumentParser(description="Full job search pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, don't write")
    parser.add_argument("--scan-only", action="store_true", help="Scan without evaluating")
    args = parser.parse_args()

    asyncio.run(run_pipeline(dry_run=args.dry_run, scan_only=args.scan_only))


if __name__ == "__main__":
    main()
