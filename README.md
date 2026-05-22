# JobHunter — Autonomous Job Search Pipeline

Fully automated job search, evaluation, CV tailoring, and notification system for working student positions in Germany. Scans multiple job boards, evaluates matches with AI, generates tailored CVs as PDFs, and sends results to your Telegram.

## How It Works

```
┌─────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐     ┌──────────┐
│  Scan   │────▶│  Filter  │────▶│ Evaluate  │────▶│ Tailor CV  │────▶│ Telegram │
│ JobSpy  │     │ + Dedup  │     │ Google AI │     │ + PDF Gen  │     │   Bot    │
└─────────┘     └──────────┘     └───────────┘     └────────────┘     └──────────┘
  Indeed          Title +          Gemma 4           JSON-patch         Score + PDF
  LinkedIn        Location         2-key rotation    controlled         + Cover Letter
  Google Jobs     History          OpenRouter         WeasyPrint        + Excel DB
                                   4-key rotation
```

### Pipeline Steps

1. **Scan** (`run_scan.py`) — Uses [python-jobspy](https://github.com/Bunsly/JobSpy) to search Indeed, LinkedIn, and Google Jobs for configured search terms and locations. Results are saved to `data/pipeline.md`.

2. **Filter & Dedup** — Title keywords (e.g. "Working Student", "Werkstudent") and location (e.g. Erlangen, Nürnberg, Munich) are checked. Jobs already seen are skipped. Descriptions are cached in `data/jd_cache.json` for evaluation.

3. **Evaluate** (`run_evaluate.py`) — Each job is scored 1-5 across 5 dimensions using Google AI Studio (Gemma 4, 1,500+ free requests/day with dual keys). Scores below threshold are logged. **Jobs requiring German B2+ are auto-rejected.**

4. **Tailor CV** — For jobs scoring ≥ threshold (default 3.5), the AI proposes targeted JSON edits (subtitle, objective keywords, skills reorder, bullet rephrasings, project selection). Code applies these edits programmatically — **the AI never touches dates, headers, or formatting.**

5. **Generate PDF** — Tailored CV is converted to an ATS-friendly A4 PDF using WeasyPrint. Dates are right-aligned using table layout.

6. **Telegram** — Every evaluation is sent as a notification. High-scoring jobs include the PDF CV and cover letter as attachments.

7. **Excel Database** — Every evaluated job is recorded in `data/jobs_database.xlsx` with scores, reasoning, and status.

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/MohamedAzzam4/JobHunter.git
cd JobHunter
python -m venv jobhunter_venv
```

**Activate the virtual environment:**
```bash
# Windows
jobhunter_venv\Scripts\activate

# Linux / macOS
source jobhunter_venv/bin/activate
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

> **⚠️ Python 3.13+ users:** `python-jobspy` internally pins `numpy==1.26.3`, which crashes on Python 3.13+. Our `requirements.txt` overrides this with `numpy>=2.4`, but if you still see `OverflowError: cannot convert longdouble infinity to integer`, run:
> ```bash
> pip install "numpy>=2.4" --force-reinstall --no-deps
> ```

**WeasyPrint (PDF generation) on Windows:**
```bash
# Install MSYS2 from https://www.msys2.org/
# Then in MSYS2 terminal:
pacman -S mingw-w64-x86_64-pango
```
The code auto-detects the GTK libraries at `C:\msys64\mingw64\bin`.

---

### 2. Configure API Keys (`.env`)

Copy the example and fill in your keys:
```bash
cp .env.example .env
```

**`.env` contents:**
```env
# OpenRouter API keys — use keys from different free accounts to increase daily quota
OpenRouter=sk-or-v1-...
OpenRouter2=sk-or-v1-...
OpenRouter3=sk-or-v1-...
OpenRouter4=sk-or-v1-...

# Google AI Studio — use keys from different Google accounts to increase daily limit
# The system rotates to the next key automatically when one hits its rate limit
GOOGLE_AI_API_KEY=AIza...
GOOGLE_AI_API_KEY2=AIza...

# Telegram Bot (see Telegram Setup below)
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=your-chat-id
```

> ⚠️ **Never commit your `.env` file.** It is already in `.gitignore`.

---

### 3. Configure Your Profile (`config/profile.yml`)

Edit your candidate info, target roles, skills, and preferences:

```yaml
candidate:
  full_name: "Your Full Name"
  short_name: "First Last"              # Used for PDF filenames
  subtitle: "Your Title | Working-Student Candidate"
  email: "your@email.com"
  phone: "+49 ..."
  location: "Your City, Germany"
  linkedin: "https://linkedin.com/in/your-profile"
  github: "https://github.com/your-username"

evaluation:
  auto_cv_threshold: 3.5               # Minimum score to generate CV (1-5)
  # German filter policy (see German Language Detection section below)
  german_filter: reject_b1_plus         # reject_b1_plus | reject_b2_plus_only | reject_unless_bilingual | accept_all
```

---

### 4. Set Up Your CV (`cv.md`)

Your base CV must follow this exact markdown format. The pipeline parses section headers, bold titles, italic subtitles, and bullet points — **changing the format will break the parser.**

```markdown
# **YOUR NAME**

**Your Title | Working-Student Candidate**

**Location:** City, Country
**Email:** your@email.com
**Phone:** +49 ...
**Links:** [LinkedIn](https://...) | [GitHub](https://...)

## **OBJECTIVE**

A 1-2 sentence objective tailored by the AI for each job.

## **EDUCATION**

**M.Sc. in Your Degree**

*Your University, Country*

*Start Date – End Date (Expected)*

**B.Sc. in Your Degree**

*Your University, Country*

*Start Date – End Date*

* **Honors:** Your honors
* **Relevant Coursework:** Course1, Course2, ...

## **TECHNICAL SKILLS**

* **Category 1:** Skill1, Skill2, Skill3
* **Category 2:** Skill4, Skill5, Skill6

## **EXPERIENCE**

**Your Job Title**

*Company, Location | Start – End*

* Achievement bullet point 1
* Achievement bullet point 2

## **PROJECTS**

**Project Name**

* Description bullet point
* *Stack:* Tech1, Tech2, Tech3

## **CERTIFICATIONS & TRAINING**

* **Certificate Name** (Provider)

## **LANGUAGES**

* **Language1:** Level
* **Language2:** Level
```

**Important format rules:**
- Section headers must be `## **SECTION NAME**`
- Job/degree titles must be `**Bold Title**`
- Dates must be `*Italic with a year*` (e.g., `*2024 – Present*`)
- For experience, combine company and date on one line with `|`: `*Company, Location | 2024 – Present*`
- For education, put university and date on separate italic lines
- Bullet points use `* ` (asterisk + space)
- **EDUCATION**, **CERTIFICATIONS & TRAINING**, and **LANGUAGES** sections are never modified by the AI

> 💡 **Don't want to write it manually?** Copy-paste this prompt to any AI (ChatGPT, Claude, Gemini) along with your existing CV:
>
> *"Convert my CV into this exact markdown format. Keep all section headers as `## **SECTION NAME**`. Use `**bold**` for job titles and degree names. Use `*italic*` for dates and company names. Use `* ` for bullet points. For experience entries, combine company and date on one line like `*Company, Location | 2024 – Present*`. For education, put university and date on separate italic lines."*
>
> Then paste the output into `cv.md`.

---

### 5. Telegram Setup

You need a Telegram bot to receive job notifications. Setup takes 30 seconds:

1. Open Telegram and message [@BotFather](https://t.me/BotFather) → send `/newbot` → follow the prompts → copy the **bot token**
2. Message [@userinfobot](https://t.me/userinfobot) → copy your **chat ID**
3. Add both to your `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_CHAT_ID=your-chat-id
   ```

**What you'll receive:**
- Score notification for every evaluated job (with German level)
- PDF CV attachment for high-scoring matches (≥ 3.5)
- Cover letter attachment
- German auto-skip notifications with `[DE]` prefix

---

### 6. Configure Search Queries (`config/portals.yml`)

This file controls **what jobs get scanned**, **how they're filtered**, and **which companies are tracked**. The defaults below are configured for working student positions in the Erlangen/Nuremberg/Munich area — **edit them to match your target roles and locations.**

> ⚠️ **You must customize this file.** The default locations, companies, and search terms are examples. Replace them with your own target cities, companies, and job titles.

#### Title & Location Filters

These filters run **after** scanning to remove irrelevant jobs before they enter the pipeline:

```yaml
title_filter:
  positive:                              # Job must contain at least one of these
    - "Working Student"
    - "Werkstudent"
    - "Werkstudentin"
    - "Student Assistant"
    - "Studentische Hilfskraft"
    - "HiWi"
    - "Studentenjob"
    - "Student Worker"
    - "Thesis Student"
    - "Intern"
  negative:                              # Job is rejected if it contains any of these
    - "Senior Engineer"
    - "Staff Engineer"
    - "Principal"
    - "Director"
    - "VP "
    - "Head of"
    - "Ausbildung"
    - "Vollzeit"
    - "Full-time"
    - "C-Level"

location_filter:
  allow:                                 # Job must be in one of these locations
    - "Erlangen"
    - "Nürnberg"
    - "Nuremberg"
    - "Munich"
    - "München"
    - "Bavaria"
    - "Bayern"
    - "Remote"
    - "Hybrid"
    - "Germany"
    - "Deutschland"
  block: []                              # Jobs in these locations are always rejected
```

#### Tracked Companies

Companies scanned via direct API. Only `scan_method: workday` entries are actively scanned — others (`websearch`) are placeholders for future implementation:

```yaml
tracked_companies:
  - name: Adidas
    careers_url: "https://careers.adidas-group.com"
    scan_method: workday
    search_terms: ["working student", "werkstudent"]
    enabled: true

  - name: Puma
    careers_url: "https://careers.puma.com"
    scan_method: workday
    search_terms: ["working student"]
    enabled: true

  - name: Bosch
    careers_url: "https://www.bosch.com/careers/"
    scan_method: workday
    search_terms: ["working student"]
    enabled: true

  # Companies with scan_method: websearch are informational placeholders.
  # No websearch scanner exists yet — these companies are covered by JobSpy.
  - name: Siemens
    scan_method: jobspy
    enabled: true
```

#### JobSpy Searches (LinkedIn, Indeed, Google Jobs)

Each entry is a separate search query. Add as many as you need:

```yaml
jobspy_searches:
  - term: "Working Student"
    location: "Erlangen, Germany"
    sites: ["indeed", "linkedin", "google"]
    results_wanted: 50
    distance_km: 50
    enabled: true

  - term: "Werkstudent"
    location: "Erlangen, Germany"
    sites: ["indeed", "linkedin", "google"]
    results_wanted: 50
    distance_km: 50
    enabled: true

  - term: "Working Student"
    location: "Munich, Germany"
    sites: ["indeed", "linkedin", "google"]
    results_wanted: 30
    enabled: true

  - term: "Student Assistant"
    location: "Erlangen, Germany"
    sites: ["indeed", "google"]
    results_wanted: 20
    distance_km: 30
    enabled: true
```

---

### 7. Run

```bash
# Scan for new jobs
python run_scan.py

# Evaluate next job
python run_evaluate.py --next

# Evaluate a batch of 10
python run_evaluate.py --batch 10

# Evaluate all pending jobs
python run_evaluate.py --all

# Full automated cycle (scan + evaluate all)
python run_all.py
```

#### Override threshold and German policy per run

Both `run_evaluate.py` and `run_all.py` accept these optional flags:

```bash
# Only generate CVs for jobs scoring 4.0 or higher
python run_evaluate.py --all --threshold 4.0

# Accept German-requiring jobs if they also require English
python run_evaluate.py --all --german-policy reject_unless_bilingual

# Accept all jobs regardless of German requirements
python run_all.py --german-policy accept_all

# Combine both flags
python run_evaluate.py --batch 10 --threshold 4.0 --german-policy reject_b2_plus_only
```

These flags override the values in `config/profile.yml` for that run only.

## Architecture

```
job_apply/
├── agents/                    # AI Agent Layer
│   ├── evaluator.py           #   Job scoring (5 dimensions + German detection)
│   ├── cv_tailor.py           #   JSON-patch CV adaptation
│   ├── cover_letter.py        #   Cover letter generation
│   ├── google_client.py       #   Google AI Studio (2-key rotation)
│   ├── smart_router.py        #   Multi-provider AI routing
│   └── openrouter_client.py   #   OpenRouter (4-key rotation)
├── scanners/                  # Data Collection Layer
│   ├── jobspy_scanner.py      #   Indeed/LinkedIn/Google Jobs via python-jobspy
│   ├── bridge.py              #   Filter → Dedup → Pipeline writer + JD cache
│   ├── workday.py             #   Direct Workday/SmartRecruiters API
│   └── base.py                #   Base scanner interface
├── utils/                     # Utility Layer
│   ├── telegram.py            #   Push notifications + file attachments
│   ├── pdf_generator.py       #   WeasyPrint PDF with table date layout
│   ├── excel_export.py        #   XLSX export with color-coded scores
│   ├── jd_cache.py            #   Cache JDs from scan time (solves Indeed 403)
│   ├── jd_fetcher.py          #   Fetch job descriptions via HTTP/Playwright
│   ├── filters.py             #   Title and location filtering
│   └── dedup.py               #   URL + company/role deduplication
├── config/
│   ├── profile.yml            #   Candidate info, skills, thresholds
│   └── portals.yml            #   Search queries and job sites
├── data/
│   ├── pipeline.md            #   Job queue ([ ] pending, [x] checked)
│   ├── jd_cache.json          #   Cached job descriptions
│   ├── jobs_database.xlsx     #   All evaluated jobs (Excel)
│   ├── below_threshold.md     #   Low-scoring jobs for review
│   ├── applications.md        #   Evaluation tracker
│   └── scan-history.tsv       #   All seen jobs with status
├── output/                    #   Generated CVs (.md + .pdf) and cover letters
├── reports/                   #   Evaluation reports per job
├── tests/                     #   91 tests (dedup, filters, bridge, German policies)
├── cv.md                      #   Your base CV (see format above)
├── .env.example               #   Template for API keys
├── run_scan.py                #   Scanner entry point
├── run_evaluate.py            #   Evaluator entry point
├── run_all.py                 #   Full pipeline (scan + evaluate)
└── requirements.txt
```

## AI Routing Strategy

The system uses multiple API keys from different free accounts to maximize daily quotas. When one key hits its rate limit, the system automatically rotates to the next.

| Task | Primary Provider | Fallback |
|------|-----------------|----------|
| **Evaluation** | Google AI Studio (Gemma 4 26B) | Gemma 4 31B → next key → OpenRouter |
| **CV Tailoring** | OpenRouter (multi-key rotation) | Key1 → Key2 → Key3 → Key4 |
| **Cover Letter** | OpenRouter (multi-key rotation) | Same rotation |

> 💡 **The more accounts/keys you add, the higher your daily quota.** Check your limits at [aistudio.google.com](https://aistudio.google.com) and [openrouter.ai](https://openrouter.ai).

## CV Tailoring Strategy

The AI uses a **JSON-patch approach** — it proposes specific edits and the code applies them:

- **Modified by AI:** Subtitle, Objective, Technical Skills order, Experience bullets, Projects selection
- **Never touched:** Education, Certifications, Languages, Header/Contact info, Dates, Company names

This prevents hallucination and keeps your CV consistent.

## German Language Detection

Jobs are classified into 4 levels:

| Level | German Required? | Example Phrases |
|-------|-----------------|------------------|
| **none** | No | No mention of German |
| **A1-A2** | No | "German is a plus", "Grundkenntnisse Deutsch" |
| **B1** | Yes | "Deutschkenntnisse B1" |
| **B2+** | Yes | "fliessende Deutschkenntnisse", "gute Deutschkenntnisse" |

Generic phrases like "gute Deutschkenntnisse" are classified as B2+ and rejected by default.

### German Filter Policies

Set the default in `config/profile.yml` under `evaluation.german_filter`, or override per run with `--german-policy`:

| Policy | Behavior |
|--------|----------|
| `reject_b1_plus` | **(Default)** Reject B1 and B2+. Only A1-A2 or no German pass through. |
| `reject_b2_plus_only` | Reject only B2+. B1 jobs are kept. |
| `reject_unless_bilingual` | Reject German jobs **unless** the JD also mentions English as a requirement. If both German and English are listed, the job is kept. |
| `accept_all` | Don't auto-reject any German level. |

## Excel Database

All evaluated jobs are tracked in `data/jobs_database.xlsx`:
- Auto-updated after each evaluation
- Color-coded scores (green ≥4.0, yellow ≥3.5, orange ≥3.0, red <3.0)
- Deduplicated by URL
- Includes all 5 scoring dimensions + reasoning

## Docker (Automated Scheduling)

```bash
docker-compose up -d
```

Runs `run_all.py` every 6 hours automatically.

## License

MIT
