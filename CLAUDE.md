# CLAUDE.md

Guidance for working in this repo. Keep it current as commands/structure change.

## What this is

A **single-user, localhost** document-scraper utility (not a multi-user SaaS). It crawls a
site, collects document links, optionally downloads them into MongoDB/GridFS, then runs AI
summarization and emergent categorization over the corpus. Calibrate severity/scope decisions
to a personal local tool, not a hosted service.

## Run

```bash
scripts/run.sh                 # bootstrap venv, run test gate, ensure Stirling-PDF, start uvicorn on :8000
scripts/run.sh --skip-tests    # skip the pytest launch gate (tight dev loops)
scripts/run.sh --force-install  # pip install even if requirements.txt is unchanged
```

App: http://127.0.0.1:8000  ·  Stirling-PDF: http://localhost:8080  ·  logs in `logs/`.

## Test

```bash
python -m pytest -q                         # default tier (integration excluded via pytest.ini addopts)
python -m pytest -m integration             # only integration (needs live MongoDB / Stirling / AI)
python -m pytest -m "integration and not live_ai"   # integration without billable AI calls
```

Markers (see `pytest.ini`): `integration` (real backends), `live_ai` (billable AI call),
`playwright` (needs `playwright install chromium`). Tests must not pollute `logs/scraper.log`
or the real `scraper.db` — `tests/conftest.py` redirects both to temp dirs before app import;
keep it that way.

## Lint & security

```bash
ruff check app/        # keep at zero
bandit -r app/         # B110 (try/except/pass) is tracked — keep at zero
```

## Stack

- **FastAPI + Jinja2 + vanilla JS**; **SSE** (`sse-starlette`) for scan/download/categorize progress.
- **SQLite** `scraper.db` — settings + scan_history (path is module-level in `app/core/database.py`).
- **MongoDB + GridFS** (optional) — documents, extracted text, summaries, categories.
- **Stirling-PDF** docker container — OCR + office→PDF conversion (also legacy `.doc` extraction).
- **AI** — summarization + iterative categorization via a configurable OpenAI/Anthropic-compatible
  endpoint; shared `app/services/ai_client.py` handles retry/backoff (honors `Retry-After`).
- Fire-and-forget async work uses `background_tasks.track()` (strong-ref pattern) — don't spawn
  bare `asyncio.create_task` without tracking.

## Docs

- `docs/architecture.md` — components, data flow, format detection.
- `docs/user-guide.md` — end-user workflows (scan, download, summarize, categorize, delete scan).
- `EVALUATION_2026-05-28.md` — latest evaluation; §9 is the living status/closure log.
