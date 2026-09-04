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

    # State machine: buffer title and subtitle across blank lines
    # until we find a date (year pattern) or other content.
    pending_title = None      # Bold line waiting for a date
    pending_subtitle = None   # Italic line (no year) waiting for a date

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Empty lines: close lists but do NOT flush title/subtitle state
        # (dates may be several blank lines after the title)
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
            pending_title, pending_subtitle = _flush_pending(
                html_parts, pending_title, pending_subtitle
            )
            if in_section:
                html_parts.append("</div>")
            header_done = True
            in_section = True
            section_name = _strip_bold(stripped[3:].strip())
            html_parts.append(f'<div class="section"><h2 class="section-title">{section_name}</h2>')
            continue

        # Check for inline title+date on one line: **Title** *Date*
        inline_match = TITLE_DATE_INLINE.match(stripped)
        if inline_match:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            pending_title, pending_subtitle = _flush_pending(
                html_parts, pending_title, pending_subtitle
            )
            title_text = inline_match.group(1)
            date_text = inline_match.group(2)
            html_parts.append(
                f'<table class="item-header"><tr>'
                f'<td>{_md_inline(title_text)}</td>'
                f'<td class="date">{date_text}</td>'
                f'</tr></table>'
            )
            continue

        # Bold title line: **Title**
        if stripped.startswith("**") and stripped.endswith("**") and not stripped.startswith("* "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            # Flush any PREVIOUS pending state before starting a new title
            pending_title, pending_subtitle = _flush_pending(
                html_parts, pending_title, pending_subtitle
            )
            # Buffer this title — we'll wait for a date or subtitle
            pending_title = stripped.strip("* ").strip()
            continue

        # Italic line (NOT a bullet "* text"): *subtitle* or *date*
        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("* "):
            inner = stripped.strip("*").strip()
            has_year = bool(re.search(r'\d{4}', inner))

            # Case 1: Combined subtitle+date — *Company, Location | 2024 – Present*
            if has_year and "|" in inner:
                parts = inner.split("|", 1)
                subtitle_part = parts[0].strip()
                date_part = parts[1].strip()

                if pending_title:
                    # Create table: Title | Date
                    html_parts.append(
                        f'<table class="item-header"><tr>'
                        f'<td>{_md_inline(pending_title)}</td>'
                        f'<td class="date">{date_part}</td>'
                        f'</tr></table>'
                    )
                    # Render company/location as subtitle below
                    html_parts.append(
                        f'<div class="item-subheader">{_md_inline(subtitle_part)}</div>'
                    )
                    pending_title = None
                    pending_subtitle = None
                else:
                    # No pending title — render as standalone subtitle
                    html_parts.append(
                        f'<div class="item-subheader">{_md_inline(inner)}</div>'
                    )
                continue

            # Case 2: Date-only line — *Apr 2026 – Apr 2028 (Expected)*
            if has_year:
                if pending_title:
                    # Create table: Title | Date
                    html_parts.append(
                        f'<table class="item-header"><tr>'
                        f'<td>{_md_inline(pending_title)}</td>'
                        f'<td class="date">{inner}</td>'
                        f'</tr></table>'
                    )
                    # If there was a buffered subtitle, render it below the table
                    if pending_subtitle:
                        html_parts.append(
                            f'<div class="item-subheader">'
                            f'{_md_inline(pending_subtitle)}</div>'
                        )
                    pending_title = None
                    pending_subtitle = None
                else:
                    # No pending title — render as standalone italic
                    html_parts.append(
                        f'<div class="item-subheader">{_md_inline(inner)}</div>'
                    )
                continue

            # Case 3: No year — it's a subtitle (university/company name)
            if pending_subtitle:
                # Flush old subtitle before storing new one
                html_parts.append(
                    f'<div class="item-subheader">{_md_inline(pending_subtitle)}</div>'
                )
            pending_subtitle = inner
            continue

        # H3 - Sub-section
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            pending_title, pending_subtitle = _flush_pending(
                html_parts, pending_title, pending_subtitle
            )
            sub = stripped[4:].strip()
            html_parts.append(
                f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
                f'{_md_inline(sub)}</div>'
            )
            continue

        # Bullet point
        if stripped.startswith("- ") or stripped.startswith("* "):
            # Flush pending state before starting bullets
            pending_title, pending_subtitle = _flush_pending(
                html_parts, pending_title, pending_subtitle
            )
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
        pending_title, pending_subtitle = _flush_pending(
            html_parts, pending_title, pending_subtitle
        )
        html_parts.append(f'<p style="font-size:10pt;margin:2px 0;">{_md_inline(stripped)}</p>')

    # Flush final state
    pending_title, pending_subtitle = _flush_pending(
        html_parts, pending_title, pending_subtitle
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


def _flush_pending(html_parts: list, pending_title: str | None,
                   pending_subtitle: str | None) -> tuple[None, None]:
    """Flush buffered title/subtitle as standalone (non-table) elements.
    
    Called when we encounter content that breaks the title→date chain
    (e.g., bullets, section headers, paragraphs) without finding a date.
    Returns (None, None) to clear both state variables.
    """
    if pending_title:
        html_parts.append(
            f'<div style="font-weight:bold;font-size:11pt;margin-top:5px;">'
            f'{_md_inline(pending_title)}</div>'
        )
    if pending_subtitle:
        html_parts.append(
            f'<div class="item-subheader">{_md_inline(pending_subtitle)}</div>'
        )
    return None, None


def _load_candidate_info() -> dict:
    """Load candidate info from profile.yml with gitignored local PII override."""
    try:
        from utils.profile_loader import load_profile_with_local

        profile = load_profile_with_local()
        candidate = profile.get("candidate", {})
        return candidate if isinstance(candidate, dict) else {}
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


def _md_to_html_cover_letter(md_content: str, candidate_name: str = "",
                              company: str = "", role: str = "",
                              contact: str = "") -> str:
    """Convert cover letter markdown to a clean, professional HTML page."""
    # Convert markdown paragraphs to HTML
    paragraphs = []
    for para in md_content.strip().split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Convert inline markdown
        para = _md_inline(para)
        # Join lines within a paragraph
        para = para.replace("\n", " ")
        paragraphs.append(f"<p>{para}</p>")

    body = "\n".join(paragraphs)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        @page {{
            size: A4;
            margin: 20mm 25mm;
        }}
        body {{
            font-family: Arial, Helvetica, sans-serif;
            line-height: 1.6;
            color: #333;
            margin: 0;
            padding: 0;
            font-size: 11pt;
        }}
        .header {{
            border-bottom: 2px solid #2c3e50;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }}
        .name {{
            font-size: 18pt;
            font-weight: bold;
            color: #2c3e50;
            margin: 0;
        }}
        .contact {{
            font-size: 9pt;
            color: #666;
            margin: 4px 0 0 0;
        }}
        .meta {{
            margin-bottom: 20px;
            font-size: 10pt;
            color: #555;
        }}
        p {{
            font-size: 11pt;
            margin: 0 0 12px 0;
            text-align: justify;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1 class="name">{candidate_name}</h1>
        <p class="contact">{contact}</p>
    </div>
    <div class="meta">
        <strong>Re:</strong> {role} — {company}
    </div>
    {body}
</body>
</html>"""


def generate_cover_letter_pdf(
    md_content: str,
    output_path: str,
    company: str = "",
    role: str = "",
) -> dict:
    """Generate a PDF cover letter from markdown content.

    Args:
        md_content: Cover letter text (plain text / light markdown)
        output_path: Where to save the PDF
        company: Company name for the header
        role: Job title for the header

    Returns:
        Dict with success status and path
    """
    candidate = _load_candidate_info()
    candidate_name = candidate.get("full_name", "Candidate")
    parts = [candidate.get("location", ""), candidate.get("email", ""), candidate.get("phone", "")]
    contact = " | ".join(p for p in parts if p)

    try:
        from weasyprint import HTML
    except ImportError:
        return {
            "success": False,
            "error": "weasyprint not installed. Run: pip install weasyprint",
        }

    try:
        html = _md_to_html_cover_letter(
            md_content, candidate_name, company, role, contact,
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        HTML(string=html).write_pdf(output_path)

        file_size = Path(output_path).stat().st_size
        logger.info("Cover letter PDF generated: %s (%d bytes)", output_path, file_size)

        return {
            "success": True,
            "output_path": output_path,
            "size_bytes": file_size,
        }

    except Exception as e:
        logger.error("Cover letter PDF generation failed: %s", e)
        return {
            "success": False,
            "error": str(e),
        }

