"""
CV Tailor agent — adapts the base CV for specific job postings.

Uses a JSON-patch approach: the AI returns structured edits (subtitle,
objective keywords, skills reorder, bullet rephrasings, project selection)
and the code applies them programmatically. This prevents the AI from
breaking formatting, moving dates, or hallucinating content.

Architecture:
  cv.md (base) → AI proposes JSON patch → code applies patch → output md → PDF
"""

import json
import logging
import re
from pathlib import Path

import yaml

from .smart_router import SmartRouter

logger = logging.getLogger(__name__)

# --- JSON-patch prompt: AI returns structured edits, not raw markdown ---
SYSTEM_PROMPT = """You are a CV tailoring assistant. You will receive the candidate's CV sections and a job description.

## YOUR TASK
Propose SPECIFIC EDITS to tailor the CV for this job. Return a JSON object with your edits.
Do NOT rewrite the entire CV. Only suggest targeted changes.

## STRICT RULES — VIOLATING ANY IS A FAILURE
1. NEVER invent experience, projects, skills, or companies the candidate doesn't have
2. NEVER change company names, job titles, dates, or numbers/percentages
3. You MAY rephrase bullet points to use keywords from the JD
4. You MAY reorder skills categories to highlight relevant skills first
5. You MAY suggest removing 1-2 least relevant bullet points or projects
6. ALWAYS keep the CV in English
7. Keep the objective under {max_objective_len} characters

## CANDIDATE'S CURRENT CV SECTIONS
{sections_text}

## OUTPUT FORMAT
You MUST respond with ONLY a valid JSON object:
```json
{{
  "subtitle": "<role-specific subtitle, max 80 chars, e.g. 'AI & Data Engineer | Working-Student Candidate'>",
  "objective": "<adapted 1-2 sentence objective using JD keywords, max {max_objective_len} chars>",
  "skills_order": ["<most relevant skill category name>", "<second most relevant>", "...all categories in order..."],
  "experience_bullets": {{
    "<exact job title from CV>": ["<rephrased bullet 1>", "<rephrased bullet 2>", "...keep same count or fewer..."]
  }},
  "projects_to_keep": ["<project name to keep>", "<project name to keep>"],
  "projects_to_remove": ["<project name to remove, if any>"]
}}
```
"""


def _extract_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object from text.
    
    Uses brace counting instead of greedy regex to avoid
    capturing text between unrelated braces.
    """
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    # Keep searching for next { after this failed one
                    next_start = text.find('{', start + 1)
                    if next_start != -1:
                        return _extract_json_object(text[next_start:])
                    return None
    return None


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


def _extract_experience_entries(section_text: str) -> list[dict]:
    """Parse EXPERIENCE section into structured entries.
    
    Returns list of dicts: {title, subtitle, bullets, raw_lines}
    """
    lines = section_text.split("\n")
    entries = []
    current = None

    for line in lines:
        stripped = line.strip()
        # Section header
        if stripped.startswith("## **"):
            continue
        # Job title line (bold)
        if stripped.startswith("**") and stripped.endswith("**") and not stripped.startswith("**Honors"):
            if current:
                entries.append(current)
            current = {"title": stripped.strip("* "), "subtitle": "", "bullets": [], "raw_header": stripped}
            continue
        # Subtitle (italic line with company/date)
        if current and not current["bullets"] and stripped.startswith("*") and not stripped.startswith("* "):
            current["subtitle"] = stripped
            continue
        # Bullet point
        if current and (stripped.startswith("* ") or stripped.startswith("- ")):
            current["bullets"].append(stripped[2:].strip())
            continue

    if current:
        entries.append(current)
    return entries


def _extract_project_entries(section_text: str) -> list[dict]:
    """Parse PROJECTS section into structured entries."""
    lines = section_text.split("\n")
    entries = []
    current = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## **"):
            continue
        if stripped.startswith("**") and stripped.endswith("**"):
            if current:
                entries.append(current)
            current = {"name": stripped.strip("* "), "bullets": [], "raw_header": stripped}
            continue
        if current and (stripped.startswith("* ") or stripped.startswith("- ")):
            current["bullets"].append(stripped[2:].strip())
            continue

    if current:
        entries.append(current)
    return entries


def _extract_skills_categories(section_text: str) -> list[dict]:
    """Parse TECHNICAL SKILLS section into categories."""
    lines = section_text.split("\n")
    categories = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## **"):
            continue
        # Pattern: * **Category:** content
        match = re.match(r"^\*\s+\*\*(.+?):\*\*\s*(.*)", stripped)
        if match:
            categories.append({
                "name": match.group(1).strip(),
                "content": match.group(2).strip(),
                "raw": stripped,
            })
    return categories


class CVTailor:
    """Adapts the base CV for specific job postings using JSON-patch editing."""

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

        # Parse sections into structured data for patch application
        self._parse_sections()

    def _load_cv(self) -> str:
        """Load base CV."""
        if not self.cv_path.exists():
            raise FileNotFoundError(f"CV not found at {self.cv_path}")
        return self.cv_path.read_text(encoding="utf-8")

    def _parse_sections(self):
        """Pre-parse sections into structured form for patch application."""
        self.experience_entries = []
        self.project_entries = []
        self.skills_categories = []
        self.objective_text = ""

        if "EXPERIENCE" in self.sections:
            self.experience_entries = _extract_experience_entries(self.sections["EXPERIENCE"])
        if "PROJECTS" in self.sections:
            self.project_entries = _extract_project_entries(self.sections["PROJECTS"])
        if "TECHNICAL SKILLS" in self.sections:
            self.skills_categories = _extract_skills_categories(self.sections["TECHNICAL SKILLS"])
        if "OBJECTIVE" in self.sections:
            # Extract just the paragraph text (skip section header)
            obj_lines = self.sections["OBJECTIVE"].split("\n")
            obj_body = [l for l in obj_lines if not l.strip().startswith("## **")]
            self.objective_text = "\n".join(obj_body).strip()

    def tailor(self, job: dict) -> dict:
        """Generate a tailored CV for a specific job.
        
        Args:
            job: Dict with 'title', 'company', 'description'
            
        Returns:
            Dict with 'success', 'output_path', 'content', 'subtitle', etc.
        """
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        description = job.get("description", "")

        if not description:
            description = f"Job Title: {title}\nCompany: {company}"

        # Build sections text for the AI prompt
        sections_for_ai = []
        for name in ["TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS"]:
            if name in self.sections:
                sections_for_ai.append(self.sections[name])
        
        # Include current objective for reference
        if self.objective_text:
            sections_for_ai.insert(0, f"## **OBJECTIVE**\n\n{self.objective_text}")

        sections_text = "\n\n".join(sections_for_ai)
        max_obj_len = len(self.objective_text) + 20  # Small buffer

        system = SYSTEM_PROMPT.format(
            sections_text=sections_text,
            max_objective_len=max_obj_len,
        )
        user = (
            f"Tailor the CV for this position:\n\n"
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

        # Parse JSON patch from response
        patch = self._parse_json_patch(response.content)
        if not patch:
            logger.warning("Failed to parse JSON patch, using base CV")
            patch = {}

        # Apply patch to build final CV
        final_cv, subtitle = self._apply_patch(patch)

        # Generate filename
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
            "subtitle": subtitle,
            "company": company,
            "title": title,
            "model_used": response.model_used,
        }

    def _parse_json_patch(self, content: str) -> dict | None:
        """Extract JSON patch from AI response."""
        # Try markdown code fence
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try to find JSON object using balanced brace matching
        json_str = _extract_json_object(content)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON patch from AI response")
            return None

    def _apply_patch(self, patch: dict) -> tuple[str, str]:
        """Apply JSON patch to the base CV. Returns (final_cv_markdown, subtitle)."""
        profile_candidate = self.profile.get("candidate", {})

        # 1. Subtitle
        subtitle = patch.get("subtitle", "")
        if not subtitle or len(subtitle) > 80:
            subtitle = profile_candidate.get(
                "subtitle", "AI Engineer | AI Agent Builder | Working-Student Candidate"
            )

        # 2. Objective
        objective = patch.get("objective", self.objective_text)
        max_len = len(self.objective_text) + 20
        if len(objective) > max_len:
            objective = self.objective_text  # Reject too-long objectives
        if not objective:
            objective = self.objective_text

        # 3. Skills reorder
        skills_order = patch.get("skills_order", [])
        skills_section = self._build_skills_section(skills_order)

        # 4. Experience bullet edits
        exp_bullets = patch.get("experience_bullets", {})
        experience_section = self._build_experience_section(exp_bullets)

        # 5. Project selection
        projects_to_keep = patch.get("projects_to_keep", [])
        projects_to_remove = patch.get("projects_to_remove", [])
        projects_section = self._build_projects_section(projects_to_keep, projects_to_remove)

        # 6. Build updated header with new subtitle
        updated_header = self._update_header_subtitle(subtitle)

        # 7. Assemble final CV
        parts = [updated_header, ""]

        # OBJECTIVE
        parts.append(f"## **OBJECTIVE**\n\n{objective}")
        parts.append("")

        # EDUCATION (always fixed)
        if "EDUCATION" in self.sections:
            parts.append(self.sections["EDUCATION"])
            parts.append("")

        # TECHNICAL SKILLS
        parts.append(skills_section)
        parts.append("")

        # EXPERIENCE
        parts.append(experience_section)
        parts.append("")

        # PROJECTS
        parts.append(projects_section)
        parts.append("")

        # CERTIFICATIONS & TRAINING (always fixed)
        if "CERTIFICATIONS & TRAINING" in self.sections:
            parts.append(self.sections["CERTIFICATIONS & TRAINING"])
            parts.append("")

        # LANGUAGES (always fixed)
        if "LANGUAGES" in self.sections:
            parts.append(self.sections["LANGUAGES"])

        return "\n\n".join(parts), subtitle

    def _update_header_subtitle(self, new_subtitle: str) -> str:
        """Replace the subtitle line in the header."""
        lines = self.header.split("\n")
        result = []
        for line in lines:
            stripped = line.strip()
            # Match the subtitle line: **AI Engineer | AI Agent Builder | ...**
            if stripped.startswith("**") and "Candidate" in stripped and "|" in stripped:
                result.append(f"**{new_subtitle}**")
            else:
                result.append(line)
        return "\n".join(result)

    def _build_skills_section(self, order: list[str]) -> str:
        """Build TECHNICAL SKILLS section with optional reordering."""
        if not self.skills_categories:
            return self.sections.get("TECHNICAL SKILLS", "")

        if order:
            # Reorder by AI suggestion — only include categories that exist
            existing = {cat["name"]: cat for cat in self.skills_categories}
            ordered = []
            for name in order:
                if name in existing:
                    ordered.append(existing.pop(name))
            # Append any remaining categories not mentioned by AI
            ordered.extend(existing.values())
        else:
            ordered = self.skills_categories

        lines = ["## **TECHNICAL SKILLS**", ""]
        for cat in ordered:
            lines.append(f"* **{cat['name']}:** {cat['content']}")
        return "\n".join(lines)

    def _build_experience_section(self, bullet_edits: dict) -> str:
        """Build EXPERIENCE section with optional bullet rephrasings."""
        if not self.experience_entries:
            return self.sections.get("EXPERIENCE", "")

        lines = ["## **EXPERIENCE**"]
        for entry in self.experience_entries:
            lines.append("")
            lines.append(entry["raw_header"])
            lines.append("")
            if entry["subtitle"]:
                lines.append(entry["subtitle"])
                lines.append("")

            # Use AI-rephrased bullets if available, else original
            if entry["title"] in bullet_edits:
                ai_bullets = bullet_edits[entry["title"]]
                # Validate: don't accept more bullets than original
                if len(ai_bullets) <= len(entry["bullets"]) + 1:
                    for bullet in ai_bullets:
                        lines.append(f"* {bullet}")
                else:
                    # AI added too many bullets — use originals
                    for bullet in entry["bullets"]:
                        lines.append(f"* {bullet}")
            else:
                for bullet in entry["bullets"]:
                    lines.append(f"* {bullet}")

        return "\n".join(lines)

    def _build_projects_section(self, keep: list[str], remove: list[str]) -> str:
        """Build PROJECTS section with optional removal."""
        if not self.project_entries:
            return self.sections.get("PROJECTS", "")

        lines = ["## **PROJECTS**"]
        for entry in self.project_entries:
            # Skip if in remove list
            if entry["name"] in remove:
                continue
            lines.append("")
            lines.append(entry["raw_header"])
            lines.append("")
            for bullet in entry["bullets"]:
                lines.append(f"* {bullet}")

        return "\n".join(lines)
