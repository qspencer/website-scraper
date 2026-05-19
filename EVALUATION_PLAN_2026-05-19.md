# Website Document Scraper — Evaluation Plan

**Author:** Q. Spencer (with Claude Opus 4.7)
**Plan date:** 2026-05-19
**Target evaluation date:** TBD (1–2 sessions)
**Prior reference:** none — this is the first formal evaluation

---

## 1. Purpose and Positioning

This document is a **plan**, not the evaluation itself. It describes *how* a full audit
of the Website Document Scraper will be conducted: the scope, the methods, the evidence
to gather, and the deliverable. The actual findings document (`EVALUATION_2026-05-19.md`)
is produced by executing this plan.

**Application context that shapes the plan.** Unlike a SaaS product, this is a
**single-user local utility**:

- Single FastAPI process started by `scripts/run.sh`, served on `127.0.0.1`
- No authentication, no authorization, no multi-tenancy, no user accounts
- SQLite for settings/scan history; optional MongoDB+GridFS for document storage
- No CI/CD, no infrastructure-as-code, no production deployment, no remote secrets store
- ~5.6k LOC Python backend, ~4.4k LOC Jinja2 templates (with substantial inline JS),
  66 LOC vanilla JS, no framework on the frontend
- 449 pytest tests across 18 files (~5.7k LOC of tests); no Karma, no Playwright e2e
- 7 commits total — early-stage project; no prior evaluation to differential against

This means **whole categories from a typical SaaS audit do not apply**: authn/authz,
CORS, rate limiting, multi-tenant data isolation, RBAC, Terraform drift, secrets
rotation, GitHub Actions workflow security. They are out of scope by construction
(see §5), not because they were deferred.

What *does* matter for a utility of this shape:

- **Defensive coding around hostile inputs**: it fetches arbitrary user-supplied URLs
  (SSRF surface — but mitigated by local-only deployment), parses untrusted HTML/PDF/
  DOCX/XLSX, and writes to the local filesystem (path traversal surface)
- **Secret hygiene** for the AI API key (Anthropic/OpenAI/compatible) and MongoDB
  credentials — stored in `runtime_settings`, persisted to SQLite
- **Subprocess and container safety**: `scripts/run.sh` shells out to `docker` to manage
  Stirling-PDF, and Playwright launches headless Chromium
- **Resource bounds**: scans can be deep, log files have already grown to 10 MB+
  (`logs/scraper.log.1`), and there is no apparent log rotation policy in code
- **Template injection / XSS risk** in the large inline-script templates
  (`results.html` is 1987 lines, `documents.html` is 976 lines) when rendering
  scraped page titles and URLs

This plan reflects that emphasis.

---

## 2. Scope

Five evaluation areas, with priority order reflecting risk-weighted value:

| # | Area | Priority | Rationale |
|---|---|---|---|
| 1 | **Code quality (incl. defensive-coding security)** | High | ~5.6k LOC backend, several files >500 LOC, no prior audit, hostile-input surface (URLs, parsed documents) |
| 2 | **Automated test quality & quantity** | High | 449 tests / 5.7k LOC across 18 files — quantity exists; quality and critical-path coverage unverified |
| 3 | **Packaging & runtime scripts** | Medium | Single `scripts/run.sh` (~200+ lines) manages venv, uvicorn, Stirling-PDF docker container, port cleanup, PID file |
| 4 | **Documentation** | Medium | `README.md`, `docs/architecture.md`, `docs/user-guide.md` — written early; accuracy after recent commits unverified |
| 5 | **Outstanding / unfinished work** | Medium | No prior eval, but commit-message follow-ups, code TODOs, and feature seams (e.g. `# Phase 3 prep` config block) to consolidate |

Each area gets a dedicated section in the final document with concrete file:line
citations, severity rating, and recommended remediation.

---

## 3. Approach per Area

### 3.1 Code Quality (and Defensive-Coding Security)

**Goal:** identify defects, anti-patterns, and risky drift that static review can catch.
For a utility, "security" here means defensive coding against hostile *inputs* and safe
handling of secrets and the local filesystem — **not** auth/authz/CORS.

**Method:**
1. **Static analysis sweep** (where tooling exists or can be added in-session):
   - `ruff check app/`, `mypy app/`, `bandit -r app/`
   - Capture counts (errors, warnings) and the top recurring rule violations
2. **Manual review of the largest / hottest files:**
   - `app/services/mongodb_service.py` (775 LOC) — credential handling, GridFS lifecycle,
     text-extraction error paths
   - `app/api/routes/downloads.py` (713 LOC) — path construction, filename sanitization,
     destination directory boundary
   - `app/api/routes/scraper.py` (529 LOC) — SSE lifecycle, request validation,
     cancellation
   - `app/services/ai_summarization_service.py` (463 LOC) — API key handling, prompt
     construction, response parsing, prompt-injection surface from scraped text
   - `app/services/scraper_service.py` (390 LOC) and `crawler_service.py` (376 LOC) —
     URL normalization, depth limits, rate limiting
3. **Architectural smells:**
   - Layering violations (routes reaching directly into services' internals;
     services calling routes)
   - Long files (≥500 LOC); flag for refactor candidates
   - Repeated patterns suggesting a missing helper (URL building, file naming,
     SSE event shaping)
4. **Defensive-coding security pass (utility-focused):**
   - **URL / SSRF surface:** user-supplied URLs are fetched server-side. Even though
     deployment is local, confirm there is no path by which a scraped page can pivot
     to e.g. `file://`, `http://169.254.169.254/`, or local services on other ports.
     `grep -n "allowed_schemes\|urlparse" app/utils/url_utils.py app/services/`
   - **Path traversal / filesystem boundary:** when downloading to local disk, every
     filename must be sanitized before joining to the destination root. Verify with
     `grep -n "os.path.join\|Path(.*) /" app/services/download_service.py app/api/routes/downloads.py`
   - **Subprocess / command injection:** `scripts/run.sh` invokes `docker`, and
     Python may shell out for Playwright. Audit any `subprocess.*` and shell strings
     built from user input.
   - **Template XSS:** `results.html` (1987 LOC) and `documents.html` (976 LOC) embed
     scraped titles, URLs, and filenames into HTML and inline `<script>` blocks. Verify
     Jinja autoescape is on AND that scraped content reaching `<script>` context is
     JSON-encoded, not string-interpolated.
   - **Secret handling:** `AI_API_KEY` and Mongo URI are stored in settings (SQLite).
     Verify they are never logged (grep log statements), never echoed to SSE clients,
     and not embedded in error responses. `grep -rn "api_key\|API_KEY\|MONGODB_URI\|password" app/`
   - **Prompt-injection awareness:** AI summarization sends scraped document text to
     the model. Document the risk (a scraped doc can instruct the model to mis-summarize
     others). At minimum: the summarizer's output should be treated as untrusted (e.g.
     not auto-acted-upon).
   - **Resource bounds:** crawl depth, total pages per scan, concurrent requests,
     download size caps, MongoDB document size — verify each has a default and a
     hard ceiling. Look for unbounded loops or unbounded list accumulation.
5. **Dependency rot:**
   - `pip-audit -r requirements.txt` — CVEs in current pins
   - Note that `requirements.txt` uses `>=` lower bounds with no upper pin — flag as a
     reproducibility concern (today's `pip install` differs from last month's)
   - Stray repo artifact: `=2.0.0` (empty file at repo root) suggests a `pip install >=2.0.0`
     redirect mishap — confirm and recommend deletion

**Evidence to produce:** a finding-per-issue table with `path:line`, severity (P0/P1/P2/P3),
and a one-sentence remediation. Group by theme.

---

### 3.2 Automated Test Quality and Quantity

**Goal:** understand the *real* state of the test suite, not just the test count.
Headline: 449 test functions, 5.7k LOC across 18 files, no E2E layer.

**Method:**
1. **Inventory:**
   - `pytest --collect-only -q | wc -l` to confirm 449
   - `pytest --cov=app --cov-report=term-missing` for coverage by file
   - Record per-file test counts: top three by size are `test_url_utils.py` (58),
     `test_mongodb.py` (57), `test_ai_summarization.py` (52). Confirm the bottom of
     the list (`test_sse_download.py` 5, `test_calculate_sizes.py` 5,
     `test_browser_service.py` 10, `test_download_service.py` 9) reflects intent and
     not thin coverage
2. **Flakiness audit:**
   - Run the suite 3× consecutively. Any test that fails in one run and passes in
     another is a *known flake*, even if it happens to pass right now.
   - Watch SSE tests (`test_sse_scraper.py`, `test_sse_download.py`), browser tests
     (`test_browser_service.py`), and any test touching live HTTP — these are the
     usual flake hotspots.
3. **Performance audit:**
   - `pytest --durations=20` — any test over 2s, and any whose duration is dominated
     by `sleep`/`waitForTimeout`-style waits
   - Confirm Playwright-touching tests don't actually launch Chromium in every run
     (or, if they do, that it's intentional)
4. **Critical-path coverage matrix.** Build a list of the user-visible workflows and
   confirm at least one test exercises each:
   - Submit URL → scan → results page populated
   - Scan with each crawl-depth option (single-page, follow internal, etc.)
   - Document type filter applied correctly (PDFs only, common docs, all files)
   - Download selected files to local directory
   - Download selected files to MongoDB (with text extraction)
   - AI summarization run against stored MongoDB docs
   - Browse scan history; re-open a past scan's results
   - Retry inaccessible items after a scan
   - Settings page round-trip (read → modify → persist → re-read)
   - JavaScript-rendered page handled via Playwright fallback
5. **Test quality smells:**
   - `assert True` / `assert ... is not None` -only assertions
   - Try/except wrapping that swallows real failures
   - Tests that only check HTTP status codes, not response shape
   - Tests that mock the system under test (false positives) — high risk in
     `test_mongodb.py` and `test_ai_summarization.py` because the real backends are
     painful to test against
   - Tests that depend on ordering or on state created by earlier tests in the file
6. **What's missing:** no smoke/e2e layer. Decide whether a small handful of
   end-to-end tests (e.g. one happy-path scan against a local fixture site, one
   MongoDB round-trip) would pay for themselves. Recommend, don't mandate.

**Evidence to produce:** overall and per-file coverage %; flake list with reproduction
counts; slow-test list with timings; critical-path coverage matrix with ✅/❌ per row.

---

### 3.3 Packaging & Runtime Scripts

**Goal:** assess `scripts/run.sh`, the Stirling-PDF docker integration, the Playwright
bootstrap, log/PID file lifecycle, and the `pip install` story.

(There is no Terraform, no GitHub Actions, no deploy pipeline. Scope here is correspondingly
narrow.)

**Method:**
1. **`scripts/run.sh`:**
   - Read in full. Note: it manages a PID file (`.uvicorn.pid`), kills anything on the
     port, and spins up a `ghcr.io/stirling-tools/stirling-pdf:2.7.2-fat` container
     pinned to CPU/memory limits.
   - Failure modes: what happens if `docker` is missing? If the venv is missing? If the
     port is held by an unrelated process? If Stirling-PDF fails to start?
   - Idempotency: re-running should be safe.
   - Cleanup: if the script is interrupted (Ctrl-C) after starting the container, is
     anything left behind?
   - Secrets: does it write any secret to disk or env in a way the user wouldn't expect?
2. **Stirling-PDF integration:**
   - The image tag is pinned (`2.7.2-fat`) — good. Confirm the consuming code
     (`app/services/stirling_pdf_service.py`) tolerates the container being absent
     (e.g. when run.sh is bypassed).
   - Failure path: what UX does the user get when Stirling-PDF is down?
3. **Python environment:**
   - `requirements.txt` uses `>=` only — no lockfile, no upper pins. Recommend a
     `pip freeze`-derived lockfile or migration to `uv` / `pip-tools` so that today's
     install is reproducible.
   - Confirm `requirements.txt` lists every actual import (e.g. `pycryptodome` is
     present but only loaded conditionally — verify it's reachable).
4. **Playwright:**
   - README says `playwright install chromium`. Confirm the code path tolerates the
     browser being missing (clear error, not a 500).
5. **Filesystem & runtime artifacts:**
   - `.uvicorn.pid` is tracked as modified in git status — should be in `.gitignore`,
     not in the working tree
   - `=2.0.0` empty file at repo root — junk; remove
   - `.coverage` file at repo root — should be in `.gitignore`
   - `scraper.db` (the SQLite file) at repo root — should be in `.gitignore` and/or
     moved under a runtime data directory; recommend a config-driven path
   - `logs/scraper.log.1` is 10 MB and `.log.2` is also 10 MB — log rotation appears
     to be size-triggered to ~10 MB but with no retention cap. Verify in
     `app/core/logging_config.py` and recommend a retention limit.
   - `downloads/` directory present at repo root — confirm `.gitignore` covers it
6. **`.gitignore` audit:** compare what's tracked-but-ignored-worthy against what's
   actually ignored. Recommend additions.

**Evidence to produce:** punch-list of files that should/shouldn't be tracked; runtime
failure-mode table for `run.sh`; reproducibility recommendation for the Python env.

---

### 3.4 Documentation

**Goal:** decide what's accurate, what's stale, what's missing.

**Method:**
1. **Inventory:**
   - `README.md` (root) — substantial, ~10 KB, written ~Mar 8; has it kept up with
     `more work` / `updates` / `new storage option` commits since?
   - `docs/architecture.md` (~16 KB, written ~Feb 2)
   - `docs/user-guide.md` (~13 KB, written ~Mar 7)
   - No `CLAUDE.md` exists — flag as a candidate addition for future LLM-assisted work
   - Inline docstrings: spot-check coverage with `interrogate` (optional)
2. **For each doc:**
   - Read fully. Mark every claim with one of: ✅ accurate, ⚠️ partially accurate
     (specifics drifted), ❌ inaccurate (will mislead a new reader)
   - Specific checks:
     - README "Tech Stack" lists Tailwind CSS — confirm it's actually used (only 66 LOC
       of JS and no obvious CSS toolchain were visible)
     - README "Configuration" section maps to actual settings in
       `app/core/config.py` and `app/services/settings_service.py`
     - `docs/architecture.md` was written before the MongoDB storage option (commit
       `20587cb new storage option`) — likely stale on data flow
     - `docs/user-guide.md` likewise predates AI summarization and MongoDB UI
3. **Missing documentation candidates:**
   - **Operational notes:** how to back up `scraper.db`, where downloads land by
     default, how to reset state, how to recover from a corrupted MongoDB connection
   - **AI integration setup:** which endpoints are known to work (Anthropic? OpenAI?
     local Ollama?), how to obtain a key, what to do when summarization fails silently
   - **Troubleshooting:** what to do when Stirling-PDF won't start, when Playwright
     is missing, when a scan hangs
   - **API docs:** FastAPI auto-publishes OpenAPI at `/docs` — confirm it's reachable
     and the schemas are non-empty
   - **Local dev getting-started:** time how long a fresh-clone → first-scan takes by
     following only the README; report the result
4. **Inline code documentation:**
   - Spot-check public functions in `mongodb_service.py`, `ai_summarization_service.py`,
     `scraper_service.py`. Watch for **stale** docstrings (worse than missing).

**Evidence to produce:** per-doc accuracy table; list of missing docs by priority;
fresh-clone onboarding time as a single number.

---

### 3.5 Outstanding / Unfinished Work

**Goal:** consolidate everything consciously deferred or unconsciously left dangling.
There is no prior evaluation, so this is built bottom-up from the code and git history.

**Method:**
1. **TODO/FIXME/XXX/HACK sweep:**
   - `git grep -nE "TODO|FIXME|XXX|HACK" -- '*.py' '*.html' '*.sh'`
   - First-pass on the repo returned zero matches — verify (the grep above is the
     canonical run). If zero is real, that's worth noting as a strength.
2. **Feature seams marked as deferred in code:**
   - `app/core/config.py` has an `# AI summarization (Phase 3 prep)` block with
     empty defaults — confirm the "phase" framing reflects what actually shipped
   - Search for `phase`, `prep`, `placeholder`, `temporary`, `for now`, `quick fix`,
     `coming soon` across the codebase
3. **Commit-message follow-ups:**
   - `git log --no-merges` is only 7 commits — read each commit body for "follow-up",
     "TODO", "note:", "leaving X for later"
4. **Working-tree changes that have not been committed:**
   - `git status` at session start shows 10 modified files including
     `mongodb_service.py`, `ai_summarization_service.py`, `text_extraction_service.py`,
     `downloads.py`, `scraper.py`, three templates, and two test files. This is
     substantial in-flight work — catalog it as part of the backlog rather than
     reviewing it as shipped code.
5. **Dangling repo artifacts** (cross-referenced with §3.3):
   - `=2.0.0` empty file
   - `.uvicorn.pid` tracked in git
   - `.coverage`, `scraper.db`, `logs/`, `downloads/` — confirm intended state
6. **Architectural follow-ups worth naming explicitly:**
   - Single-user assumption is hard-coded; if it ever needs to become multi-user,
     the settings/history/MongoDB layers all need rework. Note as a design boundary,
     not a defect.
   - SSE-based progress reporting is per-request; cancellation of an in-flight scan
     after a client disconnect — confirm behavior
   - No structured way to package/distribute the app (no `pyproject.toml`, no
     installer, no Docker image of the app itself) — recommend whichever fits the
     user's distribution plan

**Evidence to produce:** a single ordered backlog with priority, effort estimate, and
acceptance criteria.

---

## 4. Methodology

### 4.1 Parallel investigation

Areas 3.1–3.4 are *largely independent*. §3.5 must run last because it depends on
output from the others. Suggested execution order:

```
   ┌─ §3.1 Code quality (1 agent or session)        ┐
   ├─ §3.2 Test quality (1 agent or session)        │
   ├─ §3.3 Packaging & runtime (1 agent or session) │── compose final doc
   ├─ §3.4 Documentation (1 agent or session)       │
   └─ §3.5 Outstanding work (after §3.1–§3.4)       ┘
```

### 4.2 Evidence standards

Every finding must include:

1. **A concrete reference**: file path + line number (or commit SHA)
2. **A severity**: P0 (data-loss, secret leak, or "must fix before next use") /
   P1 (correctness or defensive-coding, this week) / P2 (quality / tech debt, this
   month) / P3 (nice-to-have, when convenient)
3. **A one-sentence remediation** that names the change at a level a future engineer
   could pick up cold

No finding without all three.

P0/P1/P2/P3 are *utility-calibrated*: a missing CSRF token on a localhost-only app
is not a P0. A path-traversal hole that lets a scraped HTML file overwrite arbitrary
local files **is**.

### 4.3 Deliverable

A single Markdown file at `EVALUATION_2026-05-19.md` (or `docs/EVALUATION_2026-05-19.md`
if `docs/` is preferred), structured:

```
# Evaluation 2026-05-19
## 0. Executive summary (10 bullets, mix of strengths and weaknesses)
## 1. Code quality (incl. defensive-coding security)
## 2. Automated tests
## 3. Packaging & runtime scripts
## 4. Documentation
## 5. Outstanding work
## 6. Recommended next steps (P0/P1/P2/P3, with effort estimates)
## 7. Appendix: methodology + tool commands run
```

Each section opens with a one-paragraph summary, then a findings table, then deeper
notes per finding where the table row isn't enough.

### 4.4 Tools to have available

- `ruff`, `mypy`, `bandit`, `pip-audit` (Python static analysis & security)
- `pytest`, `pytest-cov`, `pytest-repeat` (test quality)
- `interrogate` (docstring coverage — optional)
- `git grep`, `git log` (sweeps)
- `docker` (Stirling-PDF runtime check)
- `playwright` CLI (browser availability check)
- A MongoDB instance reachable from the host (for §3.2 critical-path verification of
  the storage path) — or a willingness to mock and document the gap

### 4.5 Time budget

Smaller than a typical SaaS audit because the surface is smaller (no infra, no CI,
no frontend framework):

- §3.1 Code quality: **2–3 hours**
- §3.2 Tests: **1.5–2 hours** (plus background test runs)
- §3.3 Packaging & runtime: **45 min – 1 hour**
- §3.4 Documentation: **1 hour**
- §3.5 Outstanding work: **30 min** (assembly, given prior sections complete)
- Final doc composition: **45 min – 1 hour**

**Total:** ~6–8 hours of focused work. Comfortable in a single long session, or
splittable into two (code+tests, then runtime+docs+outstanding+composition).

---

## 5. Out of Scope

Explicitly excluded from this evaluation to prevent scope creep:

1. **Architectural rewrites** — an audit identifies what's wrong, not what to build instead
2. **Vendor or framework selection** — no active decision to support
3. **User-facing UX review** — different skill set, separate exercise
4. **Performance load testing** — flagged if observable, not actively load-tested
5. **Penetration testing** — static defensive-coding review only; no active exploitation
6. **Authn/authz, multi-tenancy, RBAC, CORS hardening, server-side rate limiting** —
   not applicable to a single-user local utility; would be cargo-culting from a SaaS
   template. (If the app ever ships as a hosted service, a *separate* evaluation
   reopens these.)
7. **Infrastructure-as-code, CI/CD, deployment pipelines, secrets-manager design** —
   none of these exist; nothing to evaluate
8. **End-to-end browser test suite buildout** — recommended at most as a finding;
   not designed or executed in this evaluation

---

## 6. Risks to the Plan

| Risk | Mitigation |
|---|---|
| Tools (`ruff`, `bandit`, etc.) not installed | Each area is methodology-driven; a missing tool falls back to manual review for that one area |
| MongoDB not reachable, blocking §3.2 storage-path verification | Document the gap as a "verify before next release" item; do not skip silently |
| Working-tree has uncommitted changes (10 files dirty at plan time) | Audit a clean commit (latest `e21a3ea`), then separately note the uncommitted diff as in-flight work in §3.5 |
| Findings overlap across areas (e.g. a doc lies about a script) | Cross-link findings; never duplicate the underlying issue |
| Final doc becomes too long to read | Hard cap at 10 pages; details go in appendix |
| Working session is interrupted | Plan supports resumption — each area produces a standalone findings file that can be assembled later |

---

## 7. Acceptance Criteria for the Plan Itself

This plan is "good enough to execute" when:

- A second engineer (or future-self) can read it and start §3.1 without asking questions
- Each area's "Method" subsection lists *specific commands* or *specific files* to look at,
  not just vague guidance like "review for code quality"
- The deliverable structure (§4.3) is concrete enough that someone could stub out the
  output file from the plan alone
- The plan correctly reflects that this is a single-user utility, not a SaaS, so the
  reader does not waste time on irrelevant SaaS concerns

---

## 8. Open Questions for the Plan

1. **Cadence**: this is the first eval. Should it become recurring (every N commits?
   on each new feature?) or stay one-off until the next major milestone?
2. **Severity calibration**: P0/P1/P2/P3 boundaries above are utility-calibrated. Sanity-
   check the calibration against the user's actual risk tolerance before kicking off
   §3.1, especially for the "defensive coding" findings — what counts as P0 vs P1?
3. **MongoDB & AI prerequisites**: live MongoDB and a working AI key are *required*
   for §3.2 critical-path coverage (storage round-trip, AI summarization run). Both
   should be confirmed reachable as a pre-flight check **before** kicking off §3.1 —
   discovering mid-§3.2 that the AI endpoint is down wastes a session. Pre-flight:
   verify Mongo URI in `runtime_settings` connects, and that `AI_API_URL` +
   `AI_API_KEY` accept a trivial request.
4. **Deliverable location**: `EVALUATION_2026-05-19.md` at repo root, or
   `docs/EVALUATION_2026-05-19.md`? Match wherever future-self will look first.
5. **Working-tree treatment**: review the latest committed SHA (`e21a3ea`) and treat
   the 10 uncommitted files as backlog (§3.5), or wait for a commit and re-baseline?

Address these before kicking off execution.
