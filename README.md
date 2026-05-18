# Job Search Automation

Zero-cost, server-ready pipeline that finds working student positions in the Erlangen/Nuremberg metropolitan area, evaluates them with AI, and auto-generates tailored CVs and cover letters for top matches.

## Architecture

```
Scanners (free)          AI Agents (free OpenRouter)       Output
+-----------+            +-------------+                  +--------+
| JobSpy    |--+         | Evaluator   |-- scores 1-5 --> | Reports|
| (Indeed,  |  |         +-------------+                  +--------+
|  LinkedIn,|  +--> pipeline.md --> |                     +--------+
|  Google)  |  |                    +--> CV Tailor ------> | Output |
+-----------+  |                    |                     | (CV +  |
| Direct API|--+                    +--> Cover Letter ---> | Cover) |
| (Bosch,   |                                            +--------+
|  etc.)    |
+-----------+
```

## Quick Start

```bash
# 1. Install dependencies
py -3.13 -m pip install -r requirements.txt

# 2. Copy .env.example to .env and add your OpenRouter API key
cp .env.example .env

# 3. Run health check
py -3.13 run_health.py

# 4. Scan for jobs (dry-run first)
py -3.13 run_scan.py --dry-run

# 5. Scan for real (writes to data/pipeline.md)
py -3.13 run_scan.py

# 6. Evaluate the next job
py -3.13 run_evaluate.py --next

# 7. Evaluate a specific job URL
py -3.13 run_evaluate.py --url "https://linkedin.com/jobs/view/..."

# 8. Evaluate all pending jobs
py -3.13 run_evaluate.py --all

# 9. Full pipeline (scan + evaluate)
py -3.13 run_all.py
```

## Project Structure

```
job_apply/
  config/
    profile.yml       # Your candidate profile + preferences
    portals.yml       # Companies + search config
  scanners/
    base.py           # Abstract scanner + JobPosting dataclass
    workday.py        # Direct API scanner (Workday/SmartRecruiters)
    jobspy_scanner.py # LinkedIn/Indeed/Google via python-jobspy
    bridge.py         # Merge + filter + dedup into pipeline.md
  agents/
    openrouter_client.py  # Free AI model client (3-model fallback)
    evaluator.py          # Job scoring (5 dimensions, weighted)
    cv_tailor.py          # AI-driven CV customization
    cover_letter.py       # AI-generated cover letters
  utils/
    filters.py        # Title + location keyword filters
    dedup.py          # URL + company/role deduplication
    jd_fetcher.py     # Fetch full JD text (httpx + Playwright)
  tests/
    test_filters.py   # 18 tests for title/location filtering
    test_dedup.py     # 9 tests for deduplication
    test_bridge.py    # 6 tests for pipeline bridge
  data/
    pipeline.md       # Job queue (auto-generated)
    scan-history.tsv  # Full scan log
    applications.md   # Evaluation tracker
  reports/            # Individual evaluation reports
  output/             # Generated CVs + cover letters
  logs/               # Runtime logs
  run_health.py       # System validation
  run_scan.py         # Scanner orchestrator
  run_evaluate.py     # Evaluation + generation
  run_all.py          # Full pipeline
  cv.md               # Your CV in markdown
  .env                # API keys (git-ignored)
  Dockerfile          # Container for VPS
  docker-compose.yml  # Auto-run every 6 hours
```

## AI Models (Free Tier)

The system uses OpenRouter's free tier with automatic fallback:

1. `google/gemma-4-31b-it:free` (primary)
2. `nvidia/nvidia-nemotron-3-super:free` (backup)
3. `openai/gpt-oss-120b:free` (fallback)

~600 requests/day available across all three models.

## Scoring System

Each job is scored 1-5 on 5 weighted dimensions:

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Skills Match | 30% | Technical/non-technical skill overlap |
| Education Match | 20% | Degree relevance |
| Location | 20% | Is it in the target region? |
| Language | 20% | English-friendly? German required? |
| Growth | 10% | Learning opportunity, career value |

**Score >= 3.5** triggers automatic CV + cover letter generation.

## Docker Deployment

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f

# Pipeline runs every 6 hours automatically
```

## Testing

```bash
# Run all tests
py -3.13 -m pytest tests/ -v

# Run specific test file
py -3.13 -m pytest tests/test_filters.py -v
```

## Key Design Decisions

- **Zero cost**: All AI models are free tier. No paid APIs.
- **Idempotent**: Running scans multiple times won't create duplicates.
- **Server-ready**: Logging, Docker, cron-compatible, no GUI dependency.
- **Fault-tolerant**: Scanner errors don't kill the pipeline. Model failures trigger fallback.
- **Privacy**: `.env` secrets are git-ignored. No data leaves your machine except API calls.
