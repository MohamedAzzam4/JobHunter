"""
CV Tailor agent — adapts the base CV to match a specific job description.

Strategy: Section-based editing, NOT full rewrite.
The AI only modifies specific sections (reorder, rephrase keywords, adjust emphasis).
The header, education, and certifications are NEVER touched by the AI.
This prevents hallucination and keeps the CV consistent.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from .smart_router import SmartRouter

logger = logging.getLogger(__name__)

# --- The AI only tailors the BODY sections, not the header ---
SYSTEM_PROMPT = """You are a CV section tailoring assistant. You will receive specific SECTIONS of a CV and a job description.

## YOUR TASK
Adapt ONLY the provided sections to better match the job. Return ONLY the modified sections.

## STRICT RULES — VIOLATING ANY OF THESE IS A FAILURE
1. NEVER invent experience, projects, skills, or companies the candidate doesn't have
2. NEVER rename projects — keep the EXACT original project names (e.g. "Project Sanad", "Turjuman", etc.)
3. NEVER add new bullet points — only REPHRASE existing ones to use keywords from the JD
4. NEVER change company names, job titles, dates, or numbers/percentages in experience
5. You MAY reorder bullet points within a section to put the most relevant ones first
6. You MAY reorder the sections themselves (e.g. put PROJECTS before EXPERIENCE if more relevant)
7. You MAY remove 1-2 least relevant bullet points or projects to keep the CV concise
8. You MAY rephrase the OBJECTIVE to match the target role
9. You MAY reorganize TECHNICAL SKILLS categories to highlight relevant skills first
10. For NON-TECHNICAL roles: emphasize Office skills, communication, teaching, CRM, organization
11. ALWAYS keep the CV in English
12. Do NOT add section dividers like --- between sections
13. Do NOT repeat the candidate's name, contact info, or header — I handle that separately
14. Output ONLY the modified sections in clean markdown, nothing else
15. Keep the EXACT same section header format: ## **SECTION NAME**

## SECTIONS TO TAILOR
{sections_text}
"""


def _load_profile() -> dict:
    """Load profile.yml."""
    path = Path("config/profile.yml")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _split_cv_sections(cv_text: str) -> tuple[str, dict[str, str]]:
    """Split CV into header (name/contact) and named sections.
    
    Returns:
        (header_text, {"OBJECTIVE": "...", "EDUCATION": "...", ...})
    """
    lines = cv_text.split("\n")
    header_lines = []
    sections = {}
    current_section = None
    current_lines = []

    for line in lines:
        # Detect ## **SECTION** headers
        match = re.match(r"^## \*\*(.+?)\*\*\s*$", line.strip())
        if match:
            # Save previous section
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = match.group(1).strip().upper()
            current_lines = [line]
            continue

        if current_section is None:
            header_lines.append(line)
        else:
            current_lines.append(line)

    # Save last section
    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return "\n".join(header_lines).strip(), sections


# Sections the AI is allowed to modify (reorder, rephrase, remove items)
TAILORABLE_SECTIONS = {
    "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS",
}
# Sections that are NEVER modified — kept exactly as in cv.md
FIXED_SECTIONS = {
    "OBJECTIVE", "EDUCATION", "CERTIFICATIONS & TRAINING", "LANGUAGES",
}


class CVTailor:
    """Adapts the base CV for specific job postings using section-based editing."""

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
        self.profile = _load_profile()

        # Split CV into header + sections
        self.header, self.sections = _split_cv_sections(self.cv_content)

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

        # Extract only tailorable sections for the AI
        sections_for_ai = []
        for name, content in self.sections.items():
            if name in TAILORABLE_SECTIONS:
                sections_for_ai.append(content)

        sections_text = "\n\n".join(sections_for_ai)

        system = SYSTEM_PROMPT.format(sections_text=sections_text)
        user = (
            f"Tailor these CV sections for this position:\n\n"
            f"**Company:** {company}\n"
            f"**Role:** {title}\n\n"
            f"**Job Description:**\n{description}"
        )

        response = self.client.generate(
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

        # Clean response — remove any markdown code fences
        tailored_body = response.content
        tailored_body = re.sub(r"^```(?:markdown)?\s*\n?", "", tailored_body)
        tailored_body = re.sub(r"\n?```\s*$", "", tailored_body)
        # Remove any --- section dividers the AI might add
        tailored_body = re.sub(r"\n---\n", "\n\n", tailored_body)

        # Reassemble: header + tailored sections + fixed sections
        final_cv = self._assemble_cv(tailored_body)

        # Generate filename using short_name from profile.yml
        candidate = self.profile.get("candidate", {})
        candidate_name = candidate.get("short_name", candidate.get("full_name", "CV"))
        name_slug = re.sub(r"[^a-z0-9]+", "-", candidate_name.lower()).strip("-")
        company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:20]
        role_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:25]
        filename = f"{name_slug}_{company_slug}_{role_slug}-cv.md"
        output_path = self.output_dir / filename

        output_path.write_text(final_cv, encoding="utf-8")
        logger.info(f"Tailored CV saved: {output_path}")

        return {
            "success": True,
            "output_path": str(output_path),
            "content": final_cv,
            "company": company,
            "title": title,
            "model_used": response.model_used,
        }

    def _assemble_cv(self, tailored_body: str) -> str:
        """Reassemble the full CV: header + fixed OBJECTIVE + AI sections + fixed sections."""
        parts = [self.header, ""]

        # Add OBJECTIVE (fixed — not modified by AI)
        if "OBJECTIVE" in self.sections:
            parts.append(self.sections["OBJECTIVE"])
            parts.append("")

        # Add AI-tailored sections (SKILLS, EXPERIENCE, PROJECTS)
        parts.append(tailored_body.strip())
        parts.append("")

        # Add remaining fixed sections (EDUCATION, CERTIFICATIONS, LANGUAGES)
        for name in ["EDUCATION", "CERTIFICATIONS & TRAINING", "LANGUAGES"]:
            if name in self.sections:
                parts.append(self.sections[name])
                parts.append("")

        return "\n\n".join(parts)
