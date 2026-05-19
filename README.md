# JobHunter — Autonomous Job Search Pipeline

Fully automated job search, evaluation, CV tailoring, and notification system for working student positions in Germany. Scans multiple job boards, evaluates matches with AI, generates tailored CVs as PDFs, and sends results to your Telegram.

## How It Works

```
┌─────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐     ┌──────────┐
│  Scan   │────▶│  Filter  │────▶│ Evaluate  │────▶│ Tailor CV  │────▶│ Telegram │
│ JobSpy  │     │ + Dedup  │     │ Google AI │     │ + PDF Gen  │     │   Bot    │
└─────────┘     └──────────┘     └───────────┘     └────────────┘     └──────────┘
  Indeed          Title +          Gemma 4           Section-based      Score + PDF
  LinkedIn        Location         1,500 RPD         editing only       attachment
  Google Jobs     History          OpenRouter         WeasyPrint
                                   4-key rotation
```

### Pipeline Steps

1. **Scan** (`run_scan.py`) — Uses [python-jobspy](https://github.com/Bunsly/JobSpy) to search Indeed, LinkedIn, and Google Jobs for configured search terms and locations. Results are saved to `data/pipeline.md`.

2. **Filter & Dedup** — Title keywords (e.g. "Working Student", "Werkstudent") and location (e.g. Erlangen, Nürnberg, Munich) are checked. Jobs already seen are skipped. Descriptions are cached in `data/jd_cache.json` for evaluation.

3. **Evaluate** (`run_evaluate.py`) — Each job is scored 1-5 across 5 dimensions using Google AI Studio (Gemma 4, 1,500 free requests/day). Scores below threshold are logged to `data/below_threshold.md` for review.

4. **Tailor CV** — For jobs scoring ≥ threshold (default 3.5), the AI adapts your CV sections (Objective, Skills, Experience, Projects) while keeping Education, Certifications, and Languages untouched. This prevents hallucination.

5. **Generate PDF** — Tailored CV is converted to an ATS-friendly A4 PDF using WeasyPrint.

6. **Telegram** — Every evaluation is sent as a notification. High-scoring jobs include the PDF CV as an attachment.

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/MohamedAzzam4/JobHunter.git
cd JobHunter
pip install -r requirements.txt
```

**WeasyPrint (PDF generation) on Windows:**
```bash
# Install MSYS2 from https://www.msys2.org/
# Then in MSYS2 terminal:
pacman -S mingw-w64-x86_64-pango
```
The code auto-detects the GTK libraries at `C:\msys64\mingw64\bin`.

### 2. Configure

Copy and fill in your credentials:
```bash
cp .env.example .env
```

**`.env` contents:**
```
# OpenRouter API keys (create free accounts at openrouter.ai)
OpenRouter=sk-or-v1-...
OpenRouter2=sk-or-v1-...
OpenRouter3=sk-or-v1-...
OpenRouter4=sk-or-v1-...

# Google AI Studio (free at aistudio.google.com)
GOOGLE_AI_API_KEY=AIza...

# Telegram Bot (create via @BotFather on Telegram)
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=your-chat-id
```

**`config/profile.yml`** — Your candidate info (name, skills, preferences):
```yaml
candidate:
  full_name: "Your Name"
  email: "your@email.com"
  phone: "+49 ..."
  location: "Your City, Germany"
  # ... more fields
```

**`cv.md`** — Your base CV in markdown format.

**`config/portals.yml`** — Search queries, locations, and job sites to scan.

### 3. Run

```bash
# Scan for new jobs
python run_scan.py

# Evaluate next job
python run_evaluate.py --next

# Evaluate a batch of 10
python run_evaluate.py --batch 10

# Evaluate all pending jobs
python run_evaluate.py --all

# Evaluate a specific URL
python run_evaluate.py --url "https://linkedin.com/jobs/view/..."

# Full automated cycle (scan + evaluate all)
python run_all.py
```

## Architecture

```
job_apply/
├── agents/
│   ├── evaluator.py       # AI job scoring (1-5 dimensions)
│   ├── cv_tailor.py        # Section-based CV adaptation
│   ├── cover_letter.py     # Cover letter generation
│   ├── google_client.py    # Google AI Studio SDK (Gemma 4)
│   ├── smart_router.py     # Multi-provider AI routing
│   └── openrouter_client.py
├── scanners/
│   ├── jobspy_scanner.py   # Indeed/LinkedIn/Google Jobs via python-jobspy
│   ├── bridge.py           # Filter → Dedup → Pipeline writer + JD cache
│   └── base.py
├── utils/
│   ├── telegram.py         # Telegram push notifications + file sending
│   ├── pdf_generator.py    # WeasyPrint PDF CV generation
│   ├── jd_cache.py         # Cache JDs from scan time (solves Indeed 403)
│   ├── filters.py          # Title and location filtering
│   └── dedup.py            # URL + company/role deduplication
├── config/
│   ├── profile.yml         # Candidate info, skills, thresholds
│   └── portals.yml         # Search queries and job sites
├── data/
│   ├── pipeline.md         # Job queue ([ ] pending, [x] checked)
│   ├── jd_cache.json       # Cached job descriptions
│   ├── below_threshold.md  # Low-scoring jobs for review
│   └── scan-history.tsv    # All seen jobs with status
├── output/                 # Generated CVs (.md + .pdf) and cover letters
├── reports/                # Evaluation reports per job
├── cv.md                   # Your base CV
├── run_scan.py             # Scanner entry point
├── run_evaluate.py         # Evaluator entry point
├── run_all.py              # Full pipeline (scan + evaluate)
└── requirements.txt
```

## AI Routing Strategy

The system uses a tiered approach to maximize free API quotas:

| Task | Primary Provider | Fallback | Daily Quota |
|------|-----------------|----------|-------------|
| **Evaluation** | Google AI Studio (Gemma 4 26B) | Gemma 4 31B → OpenRouter | ~1,500/day |
| **CV Tailoring** | OpenRouter (4-key rotation) | Key1 → Key2 → Key3 → Key4 | ~800/day |
| **Cover Letter** | OpenRouter (4-key rotation) | Same rotation | ~800/day |

**Total: ~2,300 API calls/day for free.**

## CV Tailoring Strategy

The AI does **section-based editing**, not a full rewrite:

- **Modified by AI:** Objective, Technical Skills, Experience, Projects
  - Reorder bullet points to match job keywords
  - Rephrase bullets to use JD terminology
  - Remove least relevant items for conciseness
- **Never touched:** Education, Certifications, Languages, Header/Contact info

This prevents hallucination and keeps your CV consistent.

## German Language Detection

The system distinguishes between:
- ✅ Job **posted in German** → does NOT mean German required
- ❌ Job **explicitly requires** "fließende Deutschkenntnisse" / "Deutsch B2+" → marks as German required

Many German companies post in German but accept English-speaking working students.

## Telegram Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → get your token
2. Message [@userinfobot](https://t.me/userinfobot) → get your chat ID
3. Add both to `.env`

You'll receive:
- 📊 Score notification for every evaluated job
- 📄 PDF CV attachment for high-scoring matches
- 📋 Batch summary after evaluating multiple jobs

## Docker (Automated Scheduling)

```bash
docker-compose up -d
```

Runs `run_all.py` every 6 hours automatically.

## License

MIT
