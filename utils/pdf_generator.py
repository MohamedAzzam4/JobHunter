"""
PDF CV generator using WeasyPrint.

Converts a tailored CV (markdown) into a clean, ATS-friendly PDF.
Template based on user's CV_Example.py style:
- Arial font, A4 page
- Clean table layout for dates (ATS-safe, no flexbox/float)
- Section headers with underline
- Dates right-aligned on the same line as titles
"""

import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Auto-detect MSYS2 GTK libraries for WeasyPrint on Windows
if sys.platform == "win32":
    _gtk_paths = [
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        r"C:\msys32\mingw32\bin",
    ]
    for _p in _gtk_paths:
        if Path(_p).exists() and "WEASYPRINT_DLL_DIRECTORIES" not in os.environ:
            os.environ["WEASYPRINT_DLL_DIRECTORIES"] = _p
            logger.debug("WeasyPrint DLL path set to: %s", _p)
            break


# ── Date pattern detection ──────────────────────────────────────────────
# Matches italic date patterns like: *Apr 2026 – Apr 2028 (Expected)*
# or *2024 – Present* or *Sept 2020 – July 2025*
DATE_PATTERN = re.compile(
    r"^\*(.+(?:\d{4}).+)\*\s*$"
)

# Matches a title+date on the same line: **Title** *Date*
TITLE_DATE_INLINE = re.compile(
    r"^\*\*(.+?)\*\*\s+\*(.+?)\*\s*$"
)


def _strip_bold(text: str) -> str:
    """Remove markdown bold markers: **text** -> text"""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _md_inline(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    # **bold** -> <strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # *italic* -> <em>
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # [text](url) -> <a href>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Clean up escaped characters
    text = text.replace("\\+", "+").replace("\\&", "&")
    return text


def _is_header_line(stripped: str) -> bool:
    """Check if a line is part of the CV header (name, contact, etc.)
    These are skipped since the PDF template renders its own header."""
    header_prefixes = [
        "**AI Engineer", "**Location:", "**Email:", "**Phone:",
        "**Links:", "**Military Status:", "AI Engineer",
    ]
    for prefix in header_prefixes:
        if stripped.startswith(prefix):
            return True
    return False


def _md_to_html_cv(md_content: str, candidate_name: str = "", subtitle: str = "",
                    contact: str = "", linkedin: str = "", github: str = "") -> str:
    """Convert tailored CV markdown to ATS-friendly HTML.
    
    Uses table layout for title+date pairs (matching CV_Example.py style).
    Dates appear right-aligned on the same line as titles/companies.
    """
    lines = md_content.strip().split("\n")
    html_parts = []
    in_list = False
    in_section = False
    header_done = False
    # Buffer to detect title+date pairs across consecutive lines
    pending_title = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue

        # H1 - Name (skip — rendered by template header)
        if stripped.startswith("# "):
            continue

        # Skip header/contact lines before first section
        if not header_done and _is_header_line(stripped):
            continue

        # Skip subtitle line in header (we render it from the template)
        if not header_done and stripped.startswith("**") and "|" in stripped and "Candidate" in stripped:
            continue

        # H2 - Section header
        if stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if in_section:
                html_parts.append("</div>")
            header_done = True
            in_section = True
            section_name = _strip_bold(stripped[3:].strip())
            html_parts.append(f'<div class="section"><h2 class="section-title">{section_name}</h2>')
            pending_title = None
            continue

        # Check for inline title+date: **CRM Developer** *2021 – 2025*
        inline_match = TITLE_DATE_INLINE.match(stripped)
        if inline_match:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            title_text = inline_match.group(1)
            date_text = inline_match.group(2)
            html_parts.append(
                f'<table class="item-header"><tr>'
                f'<td>{_md_inline(title_text)}</td>'
                f'<td class="date">{date_text}</td>'
                f'</tr></table>'
            )
            pending_title = None
            continue

        # Bold title line (potential title that has a date on the next line)
        if stripped.startswith("**") and stripped.endswith("**"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            title_text = stripped.strip("* ").strip()
            # Look ahead for a date line (italic on next non-empty line)
            next_lines = _peek_ahead(lines, i + 1)
            if next_lines.get("date"):
                # Title with date on next line — buffer it
                pending_title = title_text
                continue
            else:
                # Standalone bold line (no date following)
                html_parts.append(
                    f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
                    f'{_md_inline(title_text)}</div>'
                )
                pending_title = None
                continue

        # Italic line — could be a date or a subtitle
        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("* "):
            inner = stripped.strip("*").strip()

            # If we have a pending title, this is the date line
            if pending_title:
                html_parts.append(
                    f'<table class="item-header"><tr>'
                    f'<td>{_md_inline(pending_title)}</td>'
                    f'<td class="date">{inner}</td>'
                    f'</tr></table>'
                )
                pending_title = None
                continue

            # Check if it looks like a company/location subtitle
            # (contains a location or company separator like comma)
            html_parts.append(f'<div class="item-subheader">{_md_inline(inner)}</div>')
            continue

        # Flush pending title if it wasn't followed by a date
        if pending_title:
            html_parts.append(
                f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
                f'{_md_inline(pending_title)}</div>'
            )
            pending_title = None

        # H3 - Sub-section
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            sub = stripped[4:].strip()
            html_parts.append(
                f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
                f'{_md_inline(sub)}</div>'
            )
            continue

        # Bullet point
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            bullet_text = stripped[2:].strip()
            html_parts.append(f"<li>{_md_inline(bullet_text)}</li>")
            continue

        # Regular paragraph
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        html_parts.append(f'<p style="font-size:10pt;margin:2px 0;">{_md_inline(stripped)}</p>')

    # Flush pending
    if pending_title:
        html_parts.append(
            f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
            f'{_md_inline(pending_title)}</div>'
        )
    if in_list:
        html_parts.append("</ul>")
    if in_section:
        html_parts.append("</div>")

    body = "\n".join(html_parts)

    # Build links line for header
    links_html = ""
    link_parts = []
    if linkedin:
        link_parts.append(f'<a href="{linkedin}" style="color:#2c3e50;">LinkedIn</a>')
    if github:
        link_parts.append(f'<a href="{github}" style="color:#2c3e50;">GitHub</a>')
    if link_parts:
        links_html = f'<p class="contact">{" | ".join(link_parts)}</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        @page {{
            size: A4;
            margin: 15mm;
        }}
        body {{
            font-family: Arial, Helvetica, sans-serif;
            line-height: 1.4;
            color: #333;
            margin: 0;
            padding: 0;
        }}
        .header {{
            text-align: center;
            border-bottom: 2px solid #2c3e50;
            padding-bottom: 10px;
            margin-bottom: 15px;
        }}
        .name {{
            font-size: 20pt;
            font-weight: bold;
            color: #2c3e50;
            text-transform: uppercase;
            margin: 0;
        }}
        .subtitle {{
            font-size: 12pt;
            color: #555;
            margin: 5px 0;
        }}
        .contact {{
            font-size: 9pt;
            color: #666;
            margin: 2px 0;
        }}
        .contact a {{
            color: #2c3e50;
            text-decoration: none;
        }}
        .section {{
            margin-bottom: 12px;
        }}
        .section-title {{
            font-size: 13pt;
            font-weight: bold;
            color: #2c3e50;
            border-bottom: 1px solid #ddd;
            margin-bottom: 8px;
            text-transform: uppercase;
        }}
        /* ── ATS FIX: table layout for title+date pairs ── */
        .item-header {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 5px;
        }}
        .item-header td {{
            padding: 0;
            font-weight: bold;
            font-size: 11pt;
            vertical-align: middle;
        }}
        .item-header .date {{
            text-align: right;
            font-weight: normal;
            font-style: italic;
            font-size: 10pt;
            white-space: nowrap;
        }}
        .item-subheader {{
            font-style: italic;
            font-size: 10pt;
            color: #555;
        }}
        ul {{
            margin: 5px 0;
            padding-left: 20px;
        }}
        li {{
            font-size: 10pt;
            margin-bottom: 3px;
            text-align: justify;
        }}
        p {{
            font-size: 10pt;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1 class="name">{candidate_name}</h1>
        <p class="subtitle">{subtitle}</p>
        <p class="contact">{contact}</p>
        {links_html}
    </div>
    {body}
</body>
</html>"""


def _peek_ahead(lines: list[str], start: int) -> dict:
    """Look at the next non-empty line to check if it's a date."""
    for j in range(start, min(start + 3, len(lines))):
        stripped = lines[j].strip()
        if not stripped:
            continue
        # Check if it's an italic date line
        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("* "):
            inner = stripped.strip("*").strip()
            # Must contain a year to be a date
            if re.search(r"\d{4}", inner):
                return {"date": inner}
        break
    return {}


def _load_candidate_info() -> dict:
    """Load candidate info from profile.yml."""
    try:
        import yaml
        profile_path = Path("config/profile.yml")
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            return profile.get("candidate", {})
    except Exception:
        pass
    return {}


def generate_cv_pdf(
    md_content: str,
    output_path: str,
    candidate_name: str | None = None,
    contact: str | None = None,
    subtitle: str | None = None,
) -> dict:
    """Generate a PDF CV from markdown content.
    
    Args:
        md_content: Tailored CV in markdown format
        output_path: Where to save the PDF
        candidate_name: Name for the header (loaded from profile.yml if not given)
        contact: Contact line for the header (loaded from profile.yml if not given)
        subtitle: Role-specific subtitle (loaded from profile.yml if not given)
        
    Returns:
        Dict with success status and path
    """
    candidate = _load_candidate_info()
    if not candidate_name:
        candidate_name = candidate.get("full_name", "Candidate")
    if not contact:
        parts = [candidate.get("location", ""), candidate.get("email", ""), candidate.get("phone", "")]
        contact = " | ".join(p for p in parts if p)
    if not subtitle:
        subtitle = candidate.get("subtitle", "AI Engineer | AI Agent Builder | Working-Student Candidate")
    linkedin = candidate.get("linkedin", "")
    github = candidate.get("github", "")

    try:
        from weasyprint import HTML
    except ImportError:
        return {
            "success": False,
            "error": "weasyprint not installed. Run: pip install weasyprint",
        }

    try:
        html = _md_to_html_cv(
            md_content, candidate_name, subtitle,
            contact, linkedin, github,
        )
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        HTML(string=html).write_pdf(output_path)

        file_size = Path(output_path).stat().st_size
        logger.info("PDF generated: %s (%d bytes)", output_path, file_size)

        return {
            "success": True,
            "output_path": output_path,
            "size_bytes": file_size,
        }

    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        return {
            "success": False,
            "error": str(e),
        }
