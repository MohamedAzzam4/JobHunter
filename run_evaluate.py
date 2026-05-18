"""
run_evaluate.py — Evaluate jobs from the pipeline.

Picks jobs from pipeline.md, evaluates with AI, and optionally
generates tailored CVs and cover letters for high-scoring matches.

Usage:
    python run_evaluate.py --next          Evaluate the next unchecked job
    python run_evaluate.py --all           Evaluate all unchecked jobs
    python run_evaluate.py --url <URL>     Evaluate a specific job URL
"""

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agents.evaluator import Evaluator
from agents.cv_tailor import CVTailor
from agents.cover_letter import CoverLetterWriter
from utils.jd_fetcher import fetch_jd, fetch_jd_playwright

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("evaluate")

logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)
file_handler = logging.FileHandler(logs_dir / "evaluate.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.getLogger().addHandler(file_handler)


def load_profile() -> dict:
    """Load profile.yml for threshold config."""
    path = Path("config/profile.yml")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_pending_jobs(pipeline_path: str = "data/pipeline.md") -> list[dict]:
    """Parse pipeline.md and return unchecked jobs."""
    path = Path(pipeline_path)
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    jobs = []

    # Match: - [ ] URL | Company | Title [Location]
    pattern = r"- \[ \] (https?://\S+)\s*\|\s*([^|]+)\s*\|\s*(.+?)(?:\s*\[([^\]]*)\])?\s*$"
    for match in re.finditer(pattern, text, re.MULTILINE):
        url = match.group(1).strip()
        company = match.group(2).strip()
        title = match.group(3).strip()
        location = match.group(4).strip() if match.group(4) else ""
        jobs.append({
            "url": url,
            "company": company,
            "title": title,
            "location": location,
        })

    return jobs


def mark_job_checked(url: str, pipeline_path: str = "data/pipeline.md"):
    """Mark a job as checked in pipeline.md ([ ] → [x])."""
    path = Path(pipeline_path)
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    # Escape URL for regex
    escaped_url = re.escape(url)
    text = re.sub(
        rf"- \[ \] {escaped_url}",
        f"- [x] {url}",
        text,
    )
    path.write_text(text, encoding="utf-8")


async def evaluate_job(job: dict, evaluator: Evaluator, cv_tailor: CVTailor,
                       cover_writer: CoverLetterWriter, threshold: float) -> dict:
    """Evaluate a single job and generate CV/cover letter if score is high enough."""

    logger.info(f"Evaluating: {job['company']} — {job['title']}")

    # 1. Fetch full JD if we only have URL
    if not job.get("description"):
        logger.info(f"Fetching JD from {job['url']}")
        result = await fetch_jd(job["url"])

        if not result.success:
            # Try Playwright fallback
            logger.info("Static fetch failed, trying Playwright...")
            result = await fetch_jd_playwright(job["url"])

        if result.is_expired:
            logger.warning(f"Job appears expired: {job['url']}")
            return {
                "success": False,
                "error": "Job posting expired",
                "company": job["company"],
                "title": job["title"],
            }

        if result.success:
            job["description"] = result.text
            # If title/company are Unknown (--url mode), try to extract from page
            if job.get("title") == "Unknown" and result.title:
                job["title"] = result.title
        else:
            logger.warning(f"Could not fetch JD: {result.error}")
            # Continue with minimal info -- evaluator will note the limitation

    # 2. Evaluate
    evaluation = evaluator.evaluate(job)

    if not evaluation.get("success"):
        logger.error(f"Evaluation failed: {evaluation.get('error')}")
        return evaluation

    score = evaluation.get("global_score", 0)
    logger.info(
        f"Score: {score}/5 | Recommendation: {evaluation.get('recommendation')} | "
        f"German required: {evaluation.get('german_required')}"
    )

    # 3. Generate CV + Cover Letter if score >= threshold
    if score >= threshold:
        logger.info(f"Score {score} >= {threshold} -> Generating CV and cover letter")

        cv_result = cv_tailor.tailor(job)
        if cv_result.get("success"):
            logger.info(f"Tailored CV: {cv_result['output_path']}")
            evaluation["cv_path"] = cv_result["output_path"]
        else:
            logger.warning(f"CV tailoring failed: {cv_result.get('error')}")

        cover_result = cover_writer.generate(job, evaluation)
        if cover_result.get("success"):
            logger.info(f"Cover letter: {cover_result['output_path']}")
            evaluation["cover_letter_path"] = cover_result["output_path"]
        else:
            logger.warning(f"Cover letter failed: {cover_result.get('error')}")
    else:
        logger.info(f"Score {score} < {threshold} -> Skipping CV/cover letter generation")

    # 4. Mark as checked in pipeline
    mark_job_checked(job["url"])

    return evaluation


async def run_evaluate(mode: str = "next", url: str | None = None):
    """Main evaluation runner."""
    load_dotenv()
    profile = load_profile()
    threshold = profile.get("evaluation", {}).get("auto_cv_threshold", 3.5)

    evaluator = Evaluator()
    cv_tailor = CVTailor()
    cover_writer = CoverLetterWriter()

    if mode == "url" and url:
        # Evaluate specific URL
        job = {"url": url, "company": "Unknown", "title": "Unknown"}
        result = await evaluate_job(job, evaluator, cv_tailor, cover_writer, threshold)
        return [result]

    # Get pending jobs
    pending = get_pending_jobs()
    if not pending:
        logger.info("No pending jobs in pipeline.md")
        return []

    logger.info(f"Found {len(pending)} pending jobs")

    if mode == "next":
        jobs_to_evaluate = pending[:1]
    else:  # "all"
        jobs_to_evaluate = pending

    results = []
    for i, job in enumerate(jobs_to_evaluate):
        logger.info(f"\n{'='*60}")
        logger.info(f"Job {i+1}/{len(jobs_to_evaluate)}")
        logger.info(f"{'='*60}")

        result = await evaluate_job(job, evaluator, cv_tailor, cover_writer, threshold)
        results.append(result)

        # Rate limit delay between evaluations (3 API calls per job)
        if i < len(jobs_to_evaluate) - 1:
            logger.info("Waiting 5s before next evaluation (rate limit)...")
            await asyncio.sleep(5)

    # Print summary
    logger.info(f"\n{'='*60}")
    logger.info("EVALUATION SUMMARY")
    logger.info(f"{'='*60}")
    
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    high_score = [r for r in successful if r.get("global_score", 0) >= threshold]

    logger.info(f"  Evaluated:    {len(successful)}")
    logger.info(f"  Failed:       {len(failed)}")
    logger.info(f"  Score >= {threshold}: {len(high_score)} (CV + cover letter generated)")

    for r in successful:
        marker = "[+]" if r.get("global_score", 0) >= threshold else "[ ]"
        logger.info(
            f"  {marker} {r.get('company', '?')} -- {r.get('title', '?')} "
            f"-> {r.get('global_score', '?')}/5 [{r.get('recommendation', '?')}]"
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate jobs from pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--next", action="store_true", help="Evaluate next unchecked job")
    group.add_argument("--all", action="store_true", help="Evaluate all unchecked jobs")
    group.add_argument("--url", type=str, help="Evaluate a specific job URL")
    args = parser.parse_args()

    if args.url:
        asyncio.run(run_evaluate(mode="url", url=args.url))
    elif args.all:
        asyncio.run(run_evaluate(mode="all"))
    else:
        asyncio.run(run_evaluate(mode="next"))


if __name__ == "__main__":
    main()
