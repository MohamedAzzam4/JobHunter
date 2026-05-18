"""
CV Tailor agent — adapts the base CV to match a specific job description.

Two modes:
- Technical roles: emphasize AI, engineering, programming skills
- Non-technical roles: emphasize office skills, communication, organization
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from .openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a CV tailoring assistant. Your job is to adapt an existing CV to better match a specific job posting.

## RULES
1. NEVER invent experience, projects, or skills the candidate doesn't have
2. NEVER remove education or certifications
3. You MAY reorder sections to highlight relevant experience first
4. You MAY rephrase bullet points to use keywords from the JD
5. You MAY remove irrelevant items to keep the CV concise (1-2 pages)
6. For TECHNICAL roles: emphasize AI, programming, engineering, data skills
7. For NON-TECHNICAL roles: emphasize Office skills, communication, teaching, CRM, organization
8. ALWAYS keep the CV in English
9. Output ONLY the tailored CV in clean markdown format
10. Do NOT include any commentary or explanation — just the CV

## Original CV
{cv_content}
"""


class CVTailor:
    """Adapts the base CV for specific job postings."""

    def __init__(
        self,
        cv_path: str = "cv.md",
        output_dir: str = "output",
    ):
        self.cv_path = Path(cv_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.client = OpenRouterClient()
        self.cv_content = self._load_cv()

    def _load_cv(self) -> str:
        """Load base CV."""
        if not self.cv_path.exists():
            raise FileNotFoundError(f"CV not found at {self.cv_path}")
        return self.cv_path.read_text(encoding="utf-8")

    def tailor(self, job: dict) -> dict:
        """Generate a tailored CV for a specific job.
        
        Args:
            job: Dict with 'title', 'company', 'description'
            
        Returns:
            Dict with 'success', 'output_path', 'content', etc.
        """
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        description = job.get("description", "")

        if not description:
            description = f"Job Title: {title}\nCompany: {company}"

        system = SYSTEM_PROMPT.format(cv_content=self.cv_content)
        user = (
            f"Tailor my CV for this position:\n\n"
            f"**Company:** {company}\n"
            f"**Role:** {title}\n\n"
            f"**Job Description:**\n{description}"
        )

        response = self.client.chat(
            system_prompt=system,
            user_prompt=user,
            temperature=0.3,
            max_tokens=4096,
        )

        if not response.success:
            logger.error(f"CV tailoring failed: {response.error}")
            return {
                "success": False,
                "error": response.error,
                "company": company,
                "title": title,
            }

        # Clean response — remove any markdown code fences if model wraps it
        content = response.content
        content = re.sub(r"^```(?:markdown)?\s*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)

        # Save tailored CV
        company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
        role_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30]
        filename = f"{company_slug}-{role_slug}-cv.md"
        output_path = self.output_dir / filename

        output_path.write_text(content, encoding="utf-8")
        logger.info(f"Tailored CV saved: {output_path}")

        return {
            "success": True,
            "output_path": str(output_path),
            "content": content,
            "company": company,
            "title": title,
            "model_used": response.model_used,
        }
