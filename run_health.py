"""
run_health.py — System health checker.

Validates all dependencies, config files, API connectivity,
and disk space before running the pipeline.

Usage:
    python run_health.py
"""

import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("health")


def check_env_vars() -> list[str]:
    """Check required environment variables."""
    errors = []
    load_dotenv()

    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OpenRouter")
    if not key:
        errors.append("❌ OPENROUTER_API_KEY not set in .env")
    elif not key.startswith("sk-or-"):
        errors.append("⚠️  OPENROUTER_API_KEY doesn't start with 'sk-or-' — may be invalid")
    else:
        logger.info(f"✅ OpenRouter API key found (sk-or-...{key[-4:]})")

    return errors


def check_files() -> list[str]:
    """Check required files exist."""
    errors = []
    required = [
        ("cv.md", "Your CV in markdown"),
        ("config/profile.yml", "Profile configuration"),
        ("config/portals.yml", "Portal/scanner configuration"),
    ]

    for path, desc in required:
        if Path(path).exists():
            logger.info(f"✅ {path} ({desc})")
        else:
            errors.append(f"❌ {path} missing ({desc})")

    return errors


def check_directories() -> list[str]:
    """Check/create required directories."""
    errors = []
    dirs = ["data", "reports", "output", "logs"]

    for d in dirs:
        p = Path(d)
        if p.exists():
            logger.info(f"✅ {d}/ directory exists")
        else:
            p.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 {d}/ directory created")

    return errors


def check_python_deps() -> list[str]:
    """Check Python dependencies are installed."""
    errors = []
    deps = {
        "httpx": "httpx",
        "yaml": "pyyaml",
        "dotenv": "python-dotenv",
        "pandas": "pandas",
        "rich": "rich",
    }

    for module, package in deps.items():
        try:
            __import__(module)
            logger.info(f"✅ {package}")
        except ImportError:
            errors.append(f"❌ {package} not installed (pip install {package})")

    # Check jobspy separately (different import name)
    try:
        from jobspy import scrape_jobs
        logger.info("✅ python-jobspy")
    except ImportError:
        errors.append("❌ python-jobspy not installed (pip install python-jobspy)")

    # Check playwright
    try:
        import playwright
        logger.info("✅ playwright (installed)")
    except ImportError:
        logger.info("⚠️  playwright not installed (optional, needed for PDF generation)")

    return errors


def check_openrouter() -> list[str]:
    """Test OpenRouter API connectivity."""
    errors = []
    try:
        from agents.openrouter_client import test_connection
        if test_connection():
            logger.info("✅ OpenRouter API connection works")
        else:
            errors.append("❌ OpenRouter API connection failed")
    except Exception as e:
        errors.append(f"❌ OpenRouter test error: {e}")

    return errors


def check_disk_space() -> list[str]:
    """Check available disk space."""
    errors = []
    usage = shutil.disk_usage(".")
    free_gb = usage.free / (1024 ** 3)

    if free_gb < 0.5:
        errors.append(f"❌ Low disk space: {free_gb:.1f} GB free")
    else:
        logger.info(f"✅ Disk space: {free_gb:.1f} GB free")

    return errors


def main():
    logger.info("=" * 60)
    logger.info("JOB SEARCH AUTOMATION — HEALTH CHECK")
    logger.info("=" * 60)

    all_errors = []

    logger.info("\n--- Environment Variables ---")
    all_errors.extend(check_env_vars())

    logger.info("\n--- Required Files ---")
    all_errors.extend(check_files())

    logger.info("\n--- Directories ---")
    all_errors.extend(check_directories())

    logger.info("\n--- Python Dependencies ---")
    all_errors.extend(check_python_deps())

    logger.info("\n--- Disk Space ---")
    all_errors.extend(check_disk_space())

    logger.info("\n--- OpenRouter API ---")
    all_errors.extend(check_openrouter())

    logger.info("\n" + "=" * 60)
    if all_errors:
        logger.error(f"HEALTH CHECK: {len(all_errors)} issue(s) found")
        for err in all_errors:
            logger.error(f"  {err}")
        sys.exit(1)
    else:
        logger.info("HEALTH CHECK: ✅ All systems OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
