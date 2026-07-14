"""
run_scan.py — Master scanner entry point.

Runs all configured scanners, merges results through the bridge,
and reports what was found.

Usage:
    python run_scan.py              Run full scan
    python run_scan.py --dry-run    Show results without writing to pipeline
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from scanners.workday import DirectAPIScanner
from scanners.jobspy_scanner import JobSpyScanner
from scanners.bridge import PipelineBridge
from utils.utf8_logging import get_utf8_stream_handler

# Setup logging — use UTF-8 safe handler so job titles containing chars
# like \ufeff (BOM) do not crash logging on Windows cp1252 consoles.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        get_utf8_stream_handler(sys.stdout),
    ],
)
logger = logging.getLogger("scan")

# Also log to file if logs/ exists
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)
file_handler = logging.FileHandler(logs_dir / "scan.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.getLogger().addHandler(file_handler)


def load_config() -> dict:
    """Load portals.yml config."""
    path = Path("config/portals.yml")
    if not path.exists():
        logger.error("config/portals.yml not found. Run setup first.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def run_scan(dry_run: bool = False):
    """Execute all scanners and merge results."""
    load_dotenv()
    config = load_config()

    logger.info("=" * 60)
    logger.info("Starting job scan...")
    logger.info("=" * 60)

    scan_results = []

    # 1. Workday scanner (Siemens, Adidas, Puma, Bosch)
    try:
        workday = DirectAPIScanner(config)
        result = await workday.scan()
        scan_results.append(result)
        logger.info(f"Workday: {result.job_count} jobs, {result.error_count} errors")
    except Exception as e:
        logger.error(f"Workday scanner failed entirely: {e}")

    # 2. JobSpy scanner (LinkedIn, Indeed, Google)
    try:
        jobspy = JobSpyScanner(config)
        result = await jobspy.scan()
        scan_results.append(result)
        logger.info(f"JobSpy: {result.job_count} jobs, {result.error_count} errors")
    except Exception as e:
        logger.error(f"JobSpy scanner failed entirely: {e}")

    # 3. Merge through bridge
    bridge = PipelineBridge(config)
    summary = bridge.process(scan_results, dry_run=dry_run)

    # 4. Print summary
    logger.info("=" * 60)
    logger.info("SCAN SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Total found:       {summary['total_found']}")
    logger.info(f"  Passed filters:    {summary['passed_filters']}")
    logger.info(f"  Rejected (title):  {summary['rejected_title']}")
    logger.info(f"  Rejected (loc):    {summary['rejected_location']}")
    logger.info(f"  Duplicates:        {summary['duplicates']}")
    logger.info(f"  [OK] New added:    {summary['new_added']}")

    if summary["errors"]:
        logger.warning(f"  [!] Errors ({len(summary['errors'])}):")
        for err in summary["errors"]:
            logger.warning(f"    - {err}")

    if dry_run:
        logger.info("(DRY RUN — nothing written to disk)")
        if summary["new_jobs"]:
            logger.info("\nWould add these jobs:")
            for job in summary["new_jobs"][:10]:
                logger.info(f"  - {job['company']}: {job['title']} [{job.get('location', '')}]")
            if len(summary["new_jobs"]) > 10:
                logger.info(f"  ... and {len(summary['new_jobs']) - 10} more")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Scan for working student jobs")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show results without writing to pipeline.md"
    )
    args = parser.parse_args()

    summary = asyncio.run(run_scan(dry_run=args.dry_run))
    sys.exit(0 if summary["new_added"] >= 0 else 1)


if __name__ == "__main__":
    main()
