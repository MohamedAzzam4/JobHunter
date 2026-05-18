"""
Cover Letter agent — generates a targeted cover letter for a specific job.

Connects the candidate's experience to the specific role requirements.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from .smart_router import SmartRouter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a cover letter writing assistant for a Working Student candidate.

## Candidate Background
{cv_content}

## RULES
1. Write a professional, warm cover letter — NOT corporate-speak
2. Maximum 350 words / 1 page
3. Reference 2-3 SPECIFIC requirements from the job description
4. Connect each requirement to the candidate's actual experience
5. Mention FAU Erlangen and the Autonomy Technologies program
6. If the role is non-technical, focus on transferable skills (organization, communication, data)
7. If the role is technical, focus on AI/engineering projects
8. End with enthusiasm and availability (20h/week as working student)
9. Output ONLY the cover letter text — no commentary, no subject line
10. Use English unless the job description is in German
11. Do NOT use phrases like: "passionate about", "proven track record", "leverage"
12. DO use specific numbers and outcomes from the CV when relevant
"""


class CoverLetterWriter:
    """Generates targeted cover letters for specific job postings."""

    def __init__(
        self,
        cv_path: str = "cv.md",
        output_dir: str = "output",
    ):
        self.cv_path = Path(cv_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.client = SmartRouter()
        self.cv_content = self._load_cv()

    def _load_cv(self) -> str:
        if not self.cv_path.exists():
            raise FileNotFoundError(f"CV not found at {self.cv_path}")
        return self.cv_path.read_text(encoding="utf-8")

    def generate(self, job: dict, evaluation: dict | None = None) -> dict:
        """Generate a cover letter for a specific job.
        
        Args:
            job: Dict with 'title', 'company', 'description'
            evaluation: Optional evaluation result for extra context
            
        Returns:
            Dict with 'success', 'output_path', 'content', etc.
        """
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        description = job.get("description", "")

        if not description:
            description = f"Job Title: {title}\nCompany: {company}"

        system = SYSTEM_PROMPT.format(cv_content=self.cv_content)

        user_parts = [
            f"Write a cover letter for this position:\n",
            f"**Company:** {company}",
            f"**Role:** {title}\n",
            f"**Job Description:**\n{description}",
        ]

        # Add evaluation context if available
        if evaluation and evaluation.get("success"):
            highlights = evaluation.get("match_highlights", [])
            if highlights:
                user_parts.append(
                    f"\n**Key matching strengths to emphasize:**\n"
                    + "\n".join(f"- {h}" for h in highlights)
                )
            gaps = evaluation.get("gaps", [])
            if gaps:
                user_parts.append(
                    f"\n**Gaps to address briefly (show willingness to learn):**\n"
                    + "\n".join(f"- {g}" for g in gaps)
                )

        response = self.client.generate(
            system_prompt=system,
            user_prompt="\n".join(user_parts),
            temperature=0.4,
            max_tokens=2048,
        )

        if not response.success:
            logger.error(f"Cover letter failed: {response.error}")
            return {
                "success": False,
                "error": response.error,
                "company": company,
                "title": title,
            }

        content = response.content.strip()

        # Save
        company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
        role_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30]
        filename = f"{company_slug}-{role_slug}-cover.md"
        output_path = self.output_dir / filename

        output_path.write_text(content, encoding="utf-8")
        logger.info(f"Cover letter saved: {output_path}")

        return {
            "success": True,
            "output_path": str(output_path),
            "content": content,
            "company": company,
            "title": title,
            "model_used": response.model_used,
            "word_count": len(content.split()),
        }
