# Website Document Scraper — Evaluation 2026-05-28 (re-eval)

**Author:** Q. Spencer (with Claude Opus 4.7)
**Prior evaluation:** `EVALUATION_2026-05-19.md`
**Baseline for this re-eval:** commit `e21a3ea` (the prior eval's baseline) → `969c18e` (current HEAD) = **17 commits, +7,704 / −564 LOC**
**Method:** 4 parallel audit agents (code / tests / runtime / docs) + synthesis. Live MongoDB, OpenAI (gpt-5.4), and Stirling-PDF were all reachable during the audit (pre-flight passed).

> **Document model.** This file has two parts. **§§0–7 are the frozen findings** — the audit as of 2026-05-28; treat them as a snapshot. **§9 is a living status** — updated as findings close, like the prior eval's Progress Status section. No effort/time estimates are included (per request).

---

## 0. Executive Summary

1. **Every closure from the 2026-05-19 remediation held.** All 3 P0 fixes (results.html XSS, settings log redaction, mongodb_uri masking) verified intact; the 6 waves of P1/P2 runtime + code fixes all stuck. No regressions of prior findings except two minor items below.
2. **The session's new code (~+7.7k LOC) is well-engineered.** `ai_client` never logs secrets and has proper retry/backoff; `background_tasks` correctly fixes the GC-weakref problem; the categorization pipeline is defensive (token cap, fence-stripping, "Other" bucketing, best-iteration tiebreaker); format detection + reclassification is sound.
3. **One real latent functional bug**: a hallucinated document id from the AI can abort the *entire* `accept_categorization` write **after** the UI has shown "final" — the user sees success, nothing persists (R-CODE-1, P1).
4. **One confirmed correctness-vs-docstring bug**: `_seeded_sample` uses Python's randomized `hash()`, so categorization sampling is **not** reproducible across server restarts despite the docstring's claim — verified live (R-CODE-2, P1). Both the code agent and the test agent flagged this independently.
5. **Two P1 test-coverage gaps on critical paths**: `ai_client.call_chat`'s retry/backoff loop (the resilience layer all AI features depend on) and the reclassification-persistence path are both untested (R-TEST-1, R-TEST-2).
6. **Documentation lagged the feature work** — the exact living-doc risk this re-eval's methodology anticipated. Two whole features shipped undocumented for users: **categorization** and **scan deletion** (R-DOC-1, R-DOC-2, both P1).
7. **Test suite is healthy**: 548 unit + 19 integration, 78% coverage, **zero flakiness** across 3 runs, and the integration tier genuinely exercises live OpenAI/Chromium/MongoDB/Stirling with correct teardown (no orphaned data).
8. **Three self-inflicted regressions from this session** (honest accounting): the `_classify_office_zip` `except: pass` (bandit B110 0→1), the seeded-sample docstring, and a phantom `--force-install` flag referenced in a `run.sh` comment but never implemented.
9. **Runtime has one unbounded-growth artifact**: `uvicorn.log` is append-only with no rotation (already 15 MB), unlike the rotation-capped `scraper.log`.
10. **Closure caveat**: "0 app-runtime CVEs" no longer strictly holds — `starlette 0.50.0` carries PYSEC-2026-161 (low practical impact here; fix may be blocked by the fastapi pin).

---

## 1. Delta vs 2026-05-19 (closure verification)

The re-verification loop this methodology adds. **All prior closures confirmed still in place:**

| Prior finding | Status 2026-05-28 | Evidence |
|---|---|---|
| P0 F1.1/F1.8 results.html XSS | ✅ held | `escapeHtml` escapes quotes; `addEventListener` wiring; 0 inline `onchange` with doc fields |
| P0 F1.2 settings log dump | ✅ held | `settings_service.py:39` logs key count only |
| P0 F1.3 mongodb_uri unmasked | ✅ held | `settings.py` `_mask_mongodb_uri` + round-trip guard |
| P1 fire-and-forget tasks | ✅ held | only `create_task` is inside `background_tasks.track` |
| P1 python-pptx missing | ✅ held | `requirements.txt:29` |
| P1 CVE floors (aiohttp/lxml/etc.) | ✅ held | all floors present |
| Wave-2 run.sh safety (trap, PID-match, `>>`, restart policy, readiness 180s, --skip-tests, mtime-skip, retroactive docker update) | ✅ all held | verified line-by-line |
| Wave-2 log rotation cap | ⚠️ partial | `backupCount=3` correct, but pre-existing `.log.4/.5` orphans never reclaimed |
| Wave-3 session sweeper / one-time index | ✅ held | — |
| Wave-4 integration tier + pymongo close | ✅ held | teardown noise gone |
| Wave-5 doc fixes (port 8000, Docker prereq, architecture banner, user-guide Documents page) | ✅ held | — |
| Wave-6 session_store / scheme allowlist / crawler caps / except-pass narrowing | ✅ held (except B110, below) | — |
| **"0 app-runtime CVEs"** | ❌ no longer strictly true | `starlette 0.50.0` PYSEC-2026-161 |
| **bandit B110 = 0** | ❌ regressed 0→1 | `text_extraction_service.py:37` `_classify_office_zip` |

---

## 2. Code Quality

New AI/categorization stack is the strongest code in the repo. Findings are mostly latent-correctness and polish.

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| **R-CODE-1** | **P1** | `categorization_service.py:219-228` → `mongodb_service.py:1042-1049` | Hallucinated/foreign doc-ids survive into the assignment map; a non-ObjectId id raises `InvalidId` inside the eager `UpdateOne` comprehension, aborting the **whole** `accept_categorization` write — *after* SSE already emitted "final", so UX is "succeeded" then nothing persists. | One line: filter `by_id` to `expected_ids` in `_parse_assignments_json`. Also closes R-CODE-3. |
| **R-CODE-2** | **P1** | `categorization_service.py:147` | `_seeded_sample` seeds RNG from builtin `hash(seed_key)` — randomized per-process (PYTHONHASHSEED unset). Sampling is **not** reproducible across restarts despite docstring. Verified live (two runs → different hashes). | Seed from `hashlib.sha256(seed_key.encode())`; or correct the docstring if cross-run determinism isn't actually needed. |
| R-CODE-3 | P2 | `mongodb_service.py:1031-1051` | `bulk_set_document_categories` has no `scan_url` scoping; a valid ObjectId from another scan (if echoed) would be categorized cross-scan. | Add `scan_url` to each `UpdateOne` filter. |
| R-CODE-4 | P2 | `app/main.py:33-44` | `_sweep_session_dict` deletes evicted categorize sessions (TTL / 500-cap) without cancelling the running pipeline task — orphaned task keeps spending AI quota with no consumer. | `task = s.get("task"); task and task.cancel()` before delete. |
| R-CODE-5 | P2 | `text_extraction_service.py:37` | `except Exception: pass` in `_classify_office_zip` — bandit B110 regression 0→1 (introduced this session). | Narrow to `except (zipfile.BadZipFile, KeyError)`. |
| R-CODE-6 | P3 | `categorize.py:143-150` | SSE `event_generator` has no client-disconnect cleanup; a 2nd concurrent subscriber to the same session would split the single-consumer queue. | Acceptable for single-user; optionally guard against a 2nd subscriber. |
| R-CODE-7 | P3 | venv `starlette 0.50.0` | PYSEC-2026-161 (Host-header URL reconstruction). App has no Host-based auth/routing → impact nil; fix = starlette ≥1.0.1, may be blocked by fastapi pin. | Bump when fastapi allows. |
| R-CODE-8 | P3 | `app/` (28 sites) | 28 ruff issues: 25 unused imports (F401), 2 unused vars, 1 empty f-string. | `ruff check app/ --fix` (26 auto-fixable). |

**Verified correct** (checked, no issue): `redact_url` strips query/fragment; `call_chat` doesn't leak the key in retry logs; `_check_token_budget` hard-caps before any call; `delete_scan` cascade is best-effort and refuses while sessions active (409); `convert_to_pdf` is a localhost-only HTTP POST validating `%PDF` magic; PDF validation correctly distinguishes corrupt (skip) from failed (retry).

**Static analysis:** ruff 28 (cosmetic); bandit 1 Medium (B104 bind-all, intended) + 1 B110 (R-CODE-5); pip-audit `requirements.txt` clean; installed venv 6 (5 toolchain `pip`/`py`, 1 app-runtime `starlette`).

---

## 3. Automated Tests

Healthy and grown well (449 → 548 unit + 19 integration; 78% coverage; **no flakiness** in 3 runs; integration tier is genuinely live with correct teardown). Gaps are on the new code's hardest-to-mock paths.

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| **R-TEST-1** | **P1** | `ai_client.py:231-273` (0% cov) | The entire `call_chat` retry/backoff/Retry-After loop — the resilience layer every AI feature depends on — is untested. | Unit test: `call_chat_once` returns retryable→success; assert retry count + Retry-After honoring. |
| **R-TEST-2** | **P1** | `mongodb_service.py:560-635` (uncovered) | Format-reclassification persistence (rewriting filename/extension/file_type_label on `.pdf→.html`) is untested; only the pure detector is. | Mocked reprocess test asserting `$set` updates extension+filename when `actual_ext != ext`. |
| R-TEST-3 | P1 | `test_categorization.py:50` | `test_deterministic_across_calls` only checks within-process determinism, giving false confidence on the R-CODE-2 cross-restart bug. | Add a cross-process determinism assertion once R-CODE-2 is fixed. |
| R-TEST-4 | P2 | `tests/integration/test_delete_scan_roundtrip.py:25` | No `try/finally`; a failed pre-delete assertion leaks 5 docs + category set + history row into the **live** DB. | Move provisioning into a fixture with `finally: delete_scan(...)`. |
| R-TEST-5 | P2 | `tests/conftest.py:7-16` | Unit tests inherit the app's real `logs/scraper.log` handler → categorization tests pollute the production log every run. | Point log dir at a temp dir (or clear root handlers) before importing `app.main`. |
| R-TEST-6 | P2 | `test_text_extraction.py:222` | `.doc` test mocks `_extract_docx`; no real legacy-OLE `.doc` byte stream is decoded anywhere. | Add an integration test with a small real `.doc` fixture. |
| R-TEST-7 | P2 | `background_tasks.py:31-48` (65%) | `_on_done` error-logging branch and `cancel_all` (lifespan shutdown) untested. | Test a raising coro (assert error log + discard) and `cancel_all`. |
| R-TEST-8 | P3 | `test_mongodb.py` (several) | Category/store tests assert on pymongo call-shape internals (`call_args[...]["$set"]`, op shapes) — coupling tests that break on a driver refactor though behavior is unchanged. Live round-trips already cover behavior. | Prefer observable-outcome assertions where a live test exists. |

**Coverage of new features:** categorize happy-path / cap / edit ops (rename/merge/delete) / cascade-delete / corruption detection — all ✅ (unit + live). `accept_categorization` service path ⚠️ (live-only). `.pdf→.html` persistence and `.doc` real-payload ⚠️ (untested — R-TEST-2/6).

---

## 4. Packaging / Runtime

All prior runtime closures held; new logic (memory arithmetic, retroactive `docker update`, image-tag recreate) is sound. Findings are growth/hygiene.

| # | Sev | File:line | Issue | Remediation |
|---|---|---|---|---|
| R-RUN-1 | P2 | `scripts/run.sh:185` | Comment "Use `--force-install` to bypass" references a flag that doesn't exist (introduced this session); passing it errors via the unknown-arg case. | Implement `--force-install` or delete the comment. |
| R-RUN-2 | P2 | `logs/uvicorn.log` (15 MB) | Append-only via shell redirect with no rotation — unbounded across sessions, unlike `scraper.log`. | Rotate/truncate in run.sh when > N MB, or route uvicorn through Python logging. |
| R-RUN-3 | P2 | `logs/scraper.log.4/.5` | `backupCount=3` never reclaims pre-existing `.4/.5` (~20 MB), so the "30 MB cap" is exceeded on this install. | One-time `rm logs/scraper.log.[4-9]`; optionally prune-on-startup in run.sh. |
| R-RUN-4 | P3 | `scripts/run.sh:144-152` | Under `set -e`, a failed `docker run` (e.g., port 8080 taken) aborts the whole script before uvicorn starts, even though OCR is optional. | Wrap `docker run` with `|| { echo WARNING…; }` so the optional dep doesn't gate app start. |
| R-RUN-5 | P3 | `README.md:41`, `run.sh:98` | Docker-missing degradation note says "OCR unavailable" only; Stirling is now also required for legacy `.doc` extraction. | Note `.doc` extraction also needs Stirling. |
| R-RUN-6 | P3 | `app/core/database.py:14-15` | `scraper.db` hardcoded to repo root (working tree). Fine at current scale (20 KB / 7 rows); couples data to the checkout. | Optional: settings/env-configurable path. |

**requirements.txt:** all CVE floors held; `python-docx`/`python-pptx` present; still `>=`-only with no lockfile + dev deps mixed with runtime (carried P2 from prior eval). **.gitignore:** correct and complete; no stray tracked artifacts.

---

## 5. Documentation

Prior fixes held, but the session's features outran the docs — the predicted living-doc problem.

| # | Sev | File:section | Issue | Remediation |
|---|---|---|---|---|
| **R-DOC-1** | **P1** | `README.md:5-24`, `user-guide.md` | Document categorization (Categorize button, chips, badges, edit/merge/rename, re-run) is **entirely undocumented for users**. | Add a Features bullet + a user-guide "Document Categorization" section. |
| **R-DOC-2** | **P1** | `user-guide.md` History/Documents | Scan deletion (destructive cascade) has **zero user-facing description**. | Add a "Deleting a scan" subsection noting it removes docs, summaries, categories, history row. |
| R-DOC-3 | P2 | `README.md:228-247` | API tables miss the new routes: 4 `/categorize/*`, `GET/PATCH/DELETE /categories`, `DELETE /mongodb/scan`. Live schema = 45 paths. | Add rows, or convert to "see /docs" and trim. |
| R-DOC-4 | P2 | `architecture.md:90-118,127-205` | Diagram + Core Components omit `categorization_service`, `ai_client`, `session_store`, format-detection path. | Add the missing components + categorize routes. |
| R-DOC-5 | P2 | `architecture.md:249-251` | Crawl-depth defaults still 2/5; actual 5/10 — **incomplete closure of prior 4.5/4.6** (fixed in README/user-guide, missed here). | Change to 5/10. |
| R-DOC-6 | P2 | `README.md:15`, `architecture.md:181` | Text-extraction list omits legacy `.doc` and `.pptx`; reclassification/corruption handling unmentioned. | List `.doc`/`.pptx`; add a one-line format-correction note. |
| R-DOC-7 | P2 | (repo root) | No backup/restore guidance for `scraper.db` (history) or the ~2,800-doc MongoDB corpus. | Add a "Backing up your data" section (`cp scraper.db`, `mongodump`/`mongorestore`). |
| R-DOC-8 | P3 | `SPEC_CATEGORIZATION_2026-05-20.md:5` | Header still "Status: Draft — before implementation"; M7 listed without shipped/not marker though M1–M6 shipped. | Update to "Implemented (M1–M6); M7 deferred." |
| R-DOC-9 | P3 | (repo root) | `CLAUDE.md` still absent (carried P3). | Add minimal CLAUDE.md (run cmd, test cmd, arch pointer). |
| R-DOC-10 | P3 | `architecture.md:373-386` | "Future Improvements" lists Export-to-CSV though it shipped. | Remove shipped items. |

`SPEC_VISION_2026-05-26.md` correctly labeled Draft and confirmed unimplemented — no drift.

---

## 6. Outstanding Work (consolidated backlog)

Combines this re-eval's findings with carryovers from `EVALUATION_2026-05-19.md` §8 that remain open.

**From this re-eval (new):**
- P1: R-CODE-1 (accept-crash), R-CODE-2 (seed determinism), R-TEST-1 (ai_client retry untested), R-TEST-2 (reclassify persistence untested), R-DOC-1 (categorization undocumented), R-DOC-2 (scan-delete undocumented).
- P2: R-CODE-3/4/5, R-TEST-3/4/5/6/7, R-RUN-1/2/3, R-DOC-3/4/5/6/7.
- P3: R-CODE-6/7/8, R-TEST-8, R-RUN-4/5/6, R-DOC-8/9/10.

**Carried from 2026-05-19 §8 (still open):**
- Categorization spec **M7 polish** (tooltips, empty states, cost-estimate display, diagnostics toggle).
- **Vision-LLM feature** (`SPEC_VISION_2026-05-26.md`) — M1–M7, unimplemented.
- F3.11 split `requirements-dev.txt`; F3.12 config-driven DATABASE_PATH; F5.2 "re-open past scan" decision; F5.4 commit-message convention (commit log is still `changes`/`progress`/`more work`).
- The vision-spec-anticipated improvements: same-signal-across-iterations early-stop; initial-download OCR fallback (A); Stirling-empty-vs-PyPDF2-error message clarity (B).

---

## 7. Recommended Next Steps (priority-ordered)

**Do first (P1, small + high-value):**
1. **R-CODE-1** — filter assignments to `expected_ids` (one line; prevents silent loss of an accepted categorization; also fixes R-CODE-3).
2. **R-CODE-2 + R-TEST-3** — switch `_seeded_sample` to `hashlib`; add cross-process test. Restores the reproducibility the feature claims.
3. **R-TEST-1** — cover the `ai_client` retry loop. It's the resilience path behind every AI feature and you've been hitting OpenAI timeouts in real use.
4. **R-DOC-1 + R-DOC-2** — document categorization and scan-delete for users. Two shipped features no one can discover from the docs.

**Then (P2 cluster, themed):**
- *Runtime hygiene*: R-RUN-1/2/3 (phantom flag, uvicorn.log rotation, orphaned logs) — quick.
- *Categorization hardening*: R-CODE-4 (cancel evicted tasks), R-TEST-2/6 (reclassify + `.doc` coverage).
- *Test hygiene*: R-TEST-4 (delete-scan teardown), R-TEST-5 (log pollution).
- *Doc currency*: R-DOC-3/4/5/6/7.

**Eventually (P3):** ruff --fix, starlette bump (when fastapi allows), CLAUDE.md, spec-status updates, commit-message convention.

---

## 8. Appendix: Methodology

- **Pre-flight (new this round):** verified MongoDB / OpenAI gpt-5.4 / Stirling-PDF all reachable before auditing. This is the live-dependency check the prior plan lacked.
- **Re-verification loop (new):** §1 above explicitly confirms each prior closure rather than re-auditing from scratch.
- **4 parallel agents:** code quality (+ closure verification), tests, packaging/runtime, documentation. Synthesis (§5/§6) done after.
- **Severity calibration:** single-user localhost utility. P0 = data loss / secret leak / must-fix-before-next-use; P1 = correctness, critical-path test gap, or undiscoverable shipped feature; P2 = tech debt; P3 = nice-to-have.
- **Tools:** ruff, bandit, pip-audit, pytest --cov / --durations, 3× pytest for flakiness, git grep/log, live curl + pymongo + httpx probes.
- **No budgeting/effort estimates**, per request.
- **Baseline:** `e21a3ea` → `969c18e`. Sub-agent transcripts retained under the session tmp dir for deeper drill-down on any finding.

---

## 9. Progress Status (living — update as findings close)

_Nothing closed yet; this section will track remediation the way the 2026-05-19 doc's §8 did._

| Finding | Status | Closed in |
|---|---|---|
| (all open) | — | — |
