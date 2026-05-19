"""
run_evaluate.py -- Evaluate jobs from the pipeline.

Picks jobs from pipeline.md, evaluates with AI, and optionally
generates tailored CVs (markdown + PDF) and cover letters for high-scoring matches.
Sends Telegram notifications for all evaluations.

Usage:
    python run_evaluate.py --next          Evaluate the next unchecked job
    python run_evaluate.py --all           Evaluate all unchecked jobs
    python run_evaluate.py --url <URL>     Evaluate a specific job URL
    python run_evaluate.py --batch N       Evaluate next N unchecked jobs
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
from utils.telegram import TelegramNotifier
from utils.pdf_generator import generate_cv_pdf
from utils.jd_cache import JDCache

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
    """Mark a job as checked in pipeline.md ([ ] -> [x])."""
    path = Path(pipeline_path)
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    escaped_url = re.escape(url)
    text = re.sub(
        rf"- \[ \] {escaped_url}",
        f"- [x] {url}",
        text,
    )
    path.write_text(text, encoding="utf-8")


def log_below_threshold(evaluation: dict, threshold: float):
    """Log below-threshold jobs to data/below_threshold.md for review."""
    path = Path("data/below_threshold.md")

    if not path.exists():
        path.write_text(
            "# Jobs Below Threshold\n\n"
            "These jobs scored below the auto-apply threshold. "
            "Review them to decide if you want to lower the threshold.\n\n"
            "| Score | Company | Title | German? | Recommendation | URL |\n"
            "|-------|---------|-------|---------|----------------|-----|\n",
            encoding="utf-8",
        )

    score = evaluation.get("global_score", "?")
    company = evaluation.get("company", "?")
    title = evaluation.get("title", "?")
    german = "Yes" if evaluation.get("german_required") else "No"
    recommendation = evaluation.get("recommendation", "?")
    url = evaluation.get("url", "")

    row = f"| {score} | {company} | {title} | {german} | {recommendation} | [link]({url}) |\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(row)


async def evaluate_job(
    job: dict,
    evaluator: Evaluator,
    cv_tailor: CVTailor,
    cover_writer: CoverLetterWriter,
    threshold: float,
    telegram: TelegramNotifier,
    jd_cache: JDCache | None = None,
) -> dict:
    """Evaluate a single job and generate CV/cover letter if score is high enough."""

    logger.info(f"Evaluating: {job['company']} -- {job['title']}")

    # 1. Get JD text: cache -> httpx -> playwright
    if not job.get("description"):
        # Check cache first (populated during scanning — critical for Indeed)
        if jd_cache:
            cached = jd_cache.get(job["url"])
            if cached:
                job["description"] = cached.get("description", "")
                if job.get("title") in ("Unknown", "") and cached.get("title"):
                    job["title"] = cached["title"]
                if job.get("company") in ("Unknown", "") and cached.get("company"):
                    job["company"] = cached["company"]
                logger.info("JD loaded from cache (%d chars)", len(job["description"]))

    if not job.get("description"):
        logger.info(f"Fetching JD from {job['url']}")
        result = await fetch_jd(job["url"])

        if not result.success:
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
            # Extract company + clean title from page title (LinkedIn: "Company hiring Role")
            if result.title:
                hiring_match = re.match(r"^(.+?)\s+hiring\s+(.+)", result.title)
                if hiring_match:
                    if job.get("company") in ("Unknown", ""):
                        job["company"] = hiring_match.group(1).strip()
                        logger.info(f"Company from page title: {job['company']}")
                    if job.get("title") in ("Unknown", ""):
                        job["title"] = hiring_match.group(2).strip()
                elif job.get("title") in ("Unknown", ""):
                    job["title"] = result.title
        else:
            logger.warning(f"Could not fetch JD: {result.error}")

    # 2. Evaluate
    evaluation = evaluator.evaluate(job)

    if not evaluation.get("success"):
        logger.error(f"Evaluation failed: {evaluation.get('error')}")
        return evaluation

    score = evaluation.get("global_score", 0)

    # Update job dict with AI-extracted company/title if originally missing
    if not job.get("company") or job["company"] == "Unknown":
        job["company"] = evaluation.get("company", "Unknown")
    if not job.get("title") or job["title"] == "Unknown":
        job["title"] = evaluation.get("title", "Unknown")

    logger.info(
        f"Score: {score}/5 | Company: {job['company']} | "
        f"Recommendation: {evaluation.get('recommendation')} | "
        f"German required: {evaluation.get('german_required')}"
    )

    # 3. Send Telegram notification for every evaluation
    telegram.notify_evaluation(evaluation)

    # 4. Generate CV + Cover Letter if score >= threshold
    pdf_path = None
    if score >= threshold:
        logger.info(f"Score {score} >= {threshold} -> Generating CV and cover letter")

        cv_result = cv_tailor.tailor(job)
        if cv_result.get("success"):
            logger.info(f"Tailored CV: {cv_result['output_path']}")
            evaluation["cv_path"] = cv_result["output_path"]

            # Generate PDF from tailored CV
            md_path = cv_result["output_path"]
            pdf_out = md_path.replace("-cv.md", "-cv.pdf")
            try:
                pdf_result = generate_cv_pdf(
                    md_content=cv_result.get("content", ""),
                    output_path=pdf_out,
                )
                if pdf_result.get("success"):
                    pdf_path = pdf_result["output_path"]
                    evaluation["cv_pdf_path"] = pdf_path
                    logger.info(f"CV PDF: {pdf_path}")
                else:
                    logger.warning(f"PDF generation failed: {pdf_result.get('error')}")
            except Exception as e:
                logger.warning(f"PDF skipped (WeasyPrint not available): {e}")
        else:
            logger.warning(f"CV tailoring failed: {cv_result.get('error')}")

        cover_result = cover_writer.generate(job, evaluation)
        if cover_result.get("success"):
            logger.info(f"Cover letter: {cover_result['output_path']}")
            evaluation["cover_letter_path"] = cover_result["output_path"]
        else:
            logger.warning(f"Cover letter failed: {cover_result.get('error')}")

        # Send CV via Telegram (PDF if available, otherwise markdown)
        send_path = pdf_path or evaluation.get("cv_path")
        telegram.notify_cv_generated(evaluation, send_path)
    else:
        logger.info(f"Score {score} < {threshold} -> Skipping CV/cover letter generation")
        # Log below-threshold for review
        log_below_threshold(evaluation, threshold)

    # 5. Mark as checked in pipeline
    mark_job_checked(job["url"])

    return evaluation


async def run_evaluate(mode: str = "next", url: str | None = None, batch_size: int = 1):
    """Main evaluation runner."""
    load_dotenv()
    profile = load_profile()
    threshold = profile.get("evaluation", {}).get("auto_cv_threshold", 3.5)

    evaluator = Evaluator()
    cv_tailor = CVTailor()
    cover_writer = CoverLetterWriter()
    telegram = TelegramNotifier()
    jd_cache = JDCache()

    if mode == "url" and url:
        job = {"url": url, "company": "Unknown", "title": "Unknown"}
        result = await evaluate_job(job, evaluator, cv_tailor, cover_writer, threshold, telegram, jd_cache)
        return [result]

    # Get pending jobs
    pending = get_pending_jobs()
    if not pending:
        logger.info("No pending jobs in pipeline.md")
        return []

    logger.info(f"Found {len(pending)} pending jobs")

    if mode == "next":
        jobs_to_evaluate = pending[:1]
    elif mode == "batch":
        jobs_to_evaluate = pending[:batch_size]
    else:  # "all"
        jobs_to_evaluate = pending

    results = []
    for i, job in enumerate(jobs_to_evaluate):
        logger.info(f"\n{'='*60}")
        logger.info(f"Job {i+1}/{len(jobs_to_evaluate)}")
        logger.info(f"{'='*60}")

        result = await evaluate_job(job, evaluator, cv_tailor, cover_writer, threshold, telegram, jd_cache)
        results.append(result)

        # Rate limit delay between evaluations
        if i < len(jobs_to_evaluate) - 1:
            logger.info("Waiting 3s before next evaluation (rate limit)...")
            await asyncio.sleep(3)

    # Print summary
    logger.info(f"\n{'='*60}")
    logger.info("EVALUATION SUMMARY")
    logger.info(f"{'='*60}")
    
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    high_score = [r for r in successful if r.get("global_score", 0) >= threshold]
    below = [r for r in successful if r.get("global_score", 0) < threshold]

    logger.info(f"  Evaluated:    {len(successful)}")
    logger.info(f"  Failed:       {len(failed)}")
    logger.info(f"  Score >= {threshold}: {len(high_score)} (CV + cover letter generated)")
    logger.info(f"  Score < {threshold}:  {len(below)} (logged to below_threshold.md)")

    for r in successful:
        marker = "[+]" if r.get("global_score", 0) >= threshold else "[ ]"
        logger.info(
            f"  {marker} {r.get('company', '?')} -- {r.get('title', '?')} "
            f"-> {r.get('global_score', '?')}/5 [{r.get('recommendation', '?')}]"
        )

    # Send summary to Telegram
    if results:
        summary_text = (
            f"Evaluation Batch Complete\n"
            f"Total: {len(successful)} evaluated, {len(failed)} failed\n"
            f"Above threshold: {len(high_score)} (CV generated)\n"
            f"Below threshold: {len(below)}"
        )
        telegram.send_message(summary_text)

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate jobs from pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--next", action="store_true", help="Evaluate next unchecked job")
    group.add_argument("--all", action="store_true", help="Evaluate all unchecked jobs")
    group.add_argument("--url", type=str, help="Evaluate a specific job URL")
    group.add_argument("--batch", type=int, help="Evaluate next N unchecked jobs")
    args = parser.parse_args()

    if args.url:
        asyncio.run(run_evaluate(mode="url", url=args.url))
    elif args.all:
        asyncio.run(run_evaluate(mode="all"))
    elif args.batch:
        asyncio.run(run_evaluate(mode="batch", batch_size=args.batch))
    else:
        asyncio.run(run_evaluate(mode="next"))


if __name__ == "__main__":
    main()
