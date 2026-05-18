"""
Evaluator agent — scores a job posting against the candidate's CV.

Reads cv.md + profile.yml, sends to OpenRouter with a structured evaluation
prompt, and outputs a scored report (1-5) with analysis.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .smart_router import SmartRouter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a job evaluation assistant for a Working Student candidate.

## Candidate Profile
{profile_summary}

## Candidate CV
{cv_content}

## Your Task
Evaluate how well the provided job description matches this candidate. 
Score each dimension 1-5, then provide a weighted global score.

## Scoring Dimensions
1. **Skills Match (30%)**: Do the candidate's technical or non-technical skills match?
2. **Education Match (20%)**: Is the candidate's degree (M.Sc. Autonomy Technologies, B.Sc. Computer Engineering) relevant?
3. **Location (20%)**: Is the job in Erlangen, Nurnberg, Bamberg, Forchheim, Herzogenaurach, Furth, or Munich?
4. **Language (20%)**: Does the role EXPLICITLY require German fluency (B2 or higher)? See rules below.
5. **Growth (10%)**: Learning opportunity, thesis potential, career relevance?

## IMPORTANT LANGUAGE RULES
- The candidate speaks English C1 and German A2 (basic)
- A job posting WRITTEN in German does NOT automatically mean German is required for the role
- Many German companies post in German but accept English-speaking employees
- ONLY mark german_required=true if the JD EXPLICITLY states one of these:
  - "Fliessende Deutschkenntnisse" / "Deutsch fliessend" / "verhandlungssicher Deutsch"
  - "Deutschkenntnisse mindestens B2/C1/C2"
  - "Deutsche Sprachkenntnisse erforderlich"
  - "sehr gute Deutschkenntnisse"
- If the JD only says "Grundkenntnisse Deutsch" or "Deutsch von Vorteil", that is OK (A2 is enough)
- If there is NO mention of language requirements, assume English is sufficient -> Language score = 4
- If English is explicitly mentioned as working language -> Language score = 5

## OTHER RULES
- The candidate is open to ALL working student roles, including non-technical (office, admin, data entry)
- For non-technical roles, evaluate based on transferable skills (Office, communication, English, organization)
- If location is not in the allowed cities, mark Location as 1
- Be honest and precise. Don't inflate scores.

## Output Format
You MUST respond with ONLY a JSON object in this exact format (no markdown, no explanation outside the JSON):
```json
{{
  "scores": {{
    "skills_match": <1-5>,
    "education_match": <1-5>,
    "location": <1-5>,
    "language": <1-5>,
    "growth": <1-5>
  }},
  "global_score": <weighted average, 1 decimal>,
  "summary": "<1-2 sentence summary of the role>",
  "match_highlights": ["<skill/experience that matches>", "..."],
  "gaps": ["<missing requirement>", "..."],
  "german_required": <true/false>,
  "recommendation": "<apply/consider/skip>",
  "reasoning": "<2-3 sentences explaining the score>"
}}
```"""


class Evaluator:
    """Evaluates job postings against the candidate's CV."""

    def __init__(
        self,
        cv_path: str = "cv.md",
        profile_path: str = "config/profile.yml",
        reports_dir: str = "reports",
        data_dir: str = "data",
    ):
        self.cv_path = Path(cv_path)
        self.profile_path = Path(profile_path)
        self.reports_dir = Path(reports_dir)
        self.data_dir = Path(data_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.client = SmartRouter()
        self.cv_content = self._load_cv()
        self.profile = self._load_profile()

    def _load_cv(self) -> str:
        """Load CV content."""
        if not self.cv_path.exists():
            raise FileNotFoundError(
                f"CV not found at {self.cv_path}. Create cv.md first."
            )
        return self.cv_path.read_text(encoding="utf-8")

    def _load_profile(self) -> dict:
        """Load profile config."""
        if not self.profile_path.exists():
            logger.warning("profile.yml not found, using defaults")
            return {}
        import yaml
        with open(self.profile_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _make_profile_summary(self) -> str:
        """Create a concise profile summary for the prompt."""
        p = self.profile
        candidate = p.get("candidate", {})
        prefs = p.get("preferences", {})
        skills = p.get("skills", {})

        lines = [
            f"Name: {candidate.get('full_name', 'N/A')}",
            f"Location: {candidate.get('location', 'Erlangen, Germany')}",
            f"Education: {candidate.get('degree_current', 'M.Sc.')} at {candidate.get('university', 'FAU')}",
            f"Bachelor: {candidate.get('degree_bachelor', 'B.Sc. Computer Engineering')}",
            f"Target: Working Student positions (technical AND non-technical)",
            f"Language: English C1, German A2 (cannot work in German-only roles)",
            f"Available locations: {', '.join(prefs.get('locations', ['Erlangen']))}",
            f"Technical skills: {', '.join(skills.get('technical', [])[:5])}",
            f"Non-technical skills: {', '.join(skills.get('non_technical', [])[:5])}",
        ]
        return "\n".join(lines)

    def evaluate(self, job: dict) -> dict:
        """Evaluate a single job posting.
        
        Args:
            job: Dict with at least 'title', 'company', 'url'.
                 Optionally 'description' (full JD text).
                 
        Returns:
            Evaluation dict with scores, report path, etc.
        """
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        url = job.get("url", "")
        description = job.get("description", "")

        if not description:
            logger.warning(f"No JD text for {company} - {title}. Score may be inaccurate.")
            description = f"Job Title: {title}\nCompany: {company}\nURL: {url}"

        # Build prompt
        system = SYSTEM_PROMPT.format(
            profile_summary=self._make_profile_summary(),
            cv_content=self.cv_content,
        )
        user = f"Evaluate this job posting:\n\n{description}"

        # Call AI (Google first, OpenRouter fallback)
        response = self.client.evaluate(
            system_prompt=system,
            user_prompt=user,
            temperature=0.2,
            max_tokens=2048,
        )

        if not response.success:
            logger.error(f"Evaluation failed for {company} - {title}: {response.error}")
            return {
                "success": False,
                "error": response.error,
                "company": company,
                "title": title,
            }

        # Parse JSON response
        evaluation = self._parse_response(response.content)
        if not evaluation:
            logger.error(f"Failed to parse evaluation for {company} - {title}")
            return {
                "success": False,
                "error": "Failed to parse model response",
                "raw_response": response.content[:500],
                "company": company,
                "title": title,
            }

        # Add metadata
        evaluation["company"] = company
        evaluation["title"] = title
        evaluation["url"] = url
        evaluation["model_used"] = response.model_used
        evaluation["evaluated_at"] = datetime.now().isoformat()
        evaluation["success"] = True

        # Save report
        report_path = self._save_report(evaluation)
        evaluation["report_path"] = str(report_path)

        # Update tracker
        self._update_tracker(evaluation)

        return evaluation

    def _parse_response(self, content: str) -> dict | None:
        """Parse the model's JSON response, handling various formats."""
        # Try to extract JSON from markdown code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try to find JSON object directly
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Try the whole content as JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse JSON from response: {content[:200]}...")
            return None

    def _save_report(self, evaluation: dict) -> Path:
        """Save evaluation as a markdown report."""
        # Get next report number
        existing = list(self.reports_dir.glob("*.md"))
        if existing:
            nums = []
            for f in existing:
                match = re.match(r"(\d+)-", f.name)
                if match:
                    nums.append(int(match.group(1)))
            next_num = max(nums) + 1 if nums else 1
        else:
            next_num = 1

        date = datetime.now().strftime("%Y-%m-%d")
        company_slug = re.sub(r"[^a-z0-9]+", "-", evaluation["company"].lower()).strip("-")
        filename = f"{next_num:03d}-{company_slug}-{date}.md"
        path = self.reports_dir / filename

        scores = evaluation.get("scores", {})
        report = f"""# Evaluation: {evaluation['company']} — {evaluation['title']}

**Date:** {date}
**Score:** {evaluation.get('global_score', 'N/A')}/5
**URL:** {evaluation.get('url', 'N/A')}
**Model:** {evaluation.get('model_used', 'N/A')}
**Recommendation:** {evaluation.get('recommendation', 'N/A')}
**German Required:** {'Yes ⚠️' if evaluation.get('german_required') else 'No ✅'}

---

## Summary
{evaluation.get('summary', 'N/A')}

## Scores

| Dimension | Score | Weight |
|-----------|-------|--------|
| Skills Match | {scores.get('skills_match', '?')}/5 | 30% |
| Education Match | {scores.get('education_match', '?')}/5 | 20% |
| Location | {scores.get('location', '?')}/5 | 20% |
| Language | {scores.get('language', '?')}/5 | 20% |
| Growth | {scores.get('growth', '?')}/5 | 10% |
| **Global** | **{evaluation.get('global_score', '?')}/5** | |

## Match Highlights
{chr(10).join('- ' + h for h in evaluation.get('match_highlights', []))}

## Gaps
{chr(10).join('- ' + g for g in evaluation.get('gaps', []))}

## Reasoning
{evaluation.get('reasoning', 'N/A')}
"""

        path.write_text(report, encoding="utf-8")
        logger.info(f"Report saved: {path}")
        return path

    def _update_tracker(self, evaluation: dict):
        """Append evaluation to applications.md."""
        tracker_path = self.data_dir / "applications.md"

        if not tracker_path.exists():
            tracker_path.write_text(
                "# Applications Tracker\n\n"
                "| # | Date | Company | Role | Score | Status | Report |\n"
                "|---|------|---------|------|-------|--------|--------|\n",
                encoding="utf-8",
            )

        text = tracker_path.read_text(encoding="utf-8")

        # Count existing entries
        entry_count = len(re.findall(r"^\| \d+", text, re.MULTILINE))
        next_num = entry_count + 1

        date = datetime.now().strftime("%Y-%m-%d")
        score = evaluation.get("global_score", "?")
        recommendation = evaluation.get("recommendation", "evaluated")
        report_path = evaluation.get("report_path", "")
        report_name = Path(report_path).name if report_path else ""

        new_row = (
            f"| {next_num} | {date} | {evaluation['company']} | "
            f"{evaluation['title']} | {score}/5 | {recommendation} | "
            f"[{report_name}](reports/{report_name}) |\n"
        )

        text += new_row
        tracker_path.write_text(text, encoding="utf-8")
        logger.info(f"Tracker updated: #{next_num} {evaluation['company']}")
