# Document Categorization — Feature Specification

**Author:** Q. Spencer (with Claude Opus 4.7)
**Date:** 2026-05-20
**Status:** Draft — open questions in §13 should be answered before implementation
**Related:** existing AI summarization (`app/services/ai_summarization_service.py`),
Documents page (`app/templates/documents.html`)

---

## 1. Problem

Once the scraper has stored a few hundred or a few thousand documents in MongoDB,
browsing them by filename or full-text search no longer scales. The user needs a
**topical organization** of the corpus — a small set of categories that:

- emerges from the documents themselves, not from a pre-defined taxonomy,
- groups documents the way a human reviewer would,
- is small enough to be useful (≈5–15 categories, not 100),
- is balanced enough to be useful (no singleton categories; no one mega-bucket).

The AI summarization feature already produces per-document titles, short summaries,
keywords, and `document_type`. This spec proposes a new feature that consumes those
summaries to derive an emergent set of categories and assigns every document to one.

## 2. Goals

- Derive a flat set of 5–15 categories per scan from existing AI summaries.
- Assign every (summarized) document to exactly one category.
- Detect and iteratively refine bad categorizations (singletons, mega-buckets,
  too-many uncategorized).
- Make categories visible and filterable on the Documents page.
- Let the user manually rename, merge, and delete categories after the fact.

## 3. Non-goals (v1)

- **Multi-label assignment.** One doc → one category. Multi-label adds UI and
  assignment complexity; revisit if v1 proves too coarse.
- **Hierarchical categories.** Flat list only. Hierarchy is a v2 if the corpus
  size demands it.
- **Cross-scan categorization.** v1 categorizes per scan. A "global" view across
  all scans is a v2 consideration (§13.Q1).
- **Manual category creation from scratch.** v1 always starts from AI-proposed
  categories; the user edits afterward.
- **Re-categorizing documents that arrive after a category set is built.** v1
  re-runs the whole pipeline; auto-extension is v2 (§13.Q4).
- **Embedding-based clustering.** v1 uses the LLM only — no separate embedding
  model, no scikit-learn. If LLM-driven categorization proves inadequate at
  scale, embeddings become the obvious next step.

## 4. User Experience

The feature lives on the **Documents** page (`documents.html`), under the
already-existing scan selector.

### 4.1 Entry point

When a scan is selected and at least N documents (default: **10**) have AI
summaries, a new **"Categorize"** button appears next to the existing
**"Summarize" / "Retry Failed"** buttons.

When clicked, a modal opens showing the iterative process below in real-time
(SSE-driven, like scan progress).

### 4.2 The iteration UX (sketch)

```
┌─ Categorize Documents ──────────────────────────────────────────────┐
│                                                                     │
│  Iteration 1                                                        │
│  ✓ Proposing categories from 247 summaries … 8 proposed             │
│  ✓ Assigning documents … done                                       │
│  ⚠ Quality check: 3 singletons, 1 mega-category (62%)               │
│                                                                     │
│  Iteration 2                                                        │
│  ✓ Refining (merging singletons, splitting "General")               │
│  ✓ Re-assigning documents … done                                    │
│  ✓ Quality check: balanced (smallest 4%, largest 22%)               │
│                                                                     │
│  ┌─ Final categories ─────────────────────────────────────────────┐  │
│  │ Financial Reports             54 docs  ████████████  22%      │  │
│  │ Product Datasheets            48 docs  ██████████    19%      │  │
│  │ Marketing & Whitepapers       36 docs  ████████      15%      │  │
│  │ Engineering Specifications    31 docs  ███████       13%      │  │
│  │ Legal & Compliance            22 docs  █████          9%      │  │
│  │ … (5 more)                                                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  [ Accept ]  [ Discard ]  [ Edit categories before accepting ]      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.3 After acceptance

- Each document on the Documents page gets a **category badge** (similar to the
  existing extension badge).
- A new **Category** filter dropdown appears next to the extension filter.
- Clicking a category in the filter or on a badge filters to that category.
- A small "edit categories" affordance opens a panel where the user can:
  - **rename** a category,
  - **merge** two categories (drops one; documents reassigned to the survivor),
  - **delete** a category (its documents move to "Uncategorized"),
  - **re-run** the full categorization pipeline (overwrites).

### 4.4 What does NOT change

- Existing summarization, search, and CSV export keep working unchanged.
- Documents without summaries still appear in lists; they just don't get a
  category badge (and the categorization run skips them — see §6).

## 5. Functional Requirements

1. The pipeline runs per scan (`scan_url` selector). v1 does not categorize
   across scans.
2. Categorization is **AI-only** — uses the same `runtime_settings.ai_api_*`
   configuration as summarization. If AI is not configured, the Categorize
   button is hidden with a tooltip.
3. The pipeline operates **only on documents that already have a `summary_status
   == "complete"`**. Pending/failed summaries are excluded and counted as
   "Uncategorized" in the final state.
4. The pipeline runs in the background (uses `app/services/background_tasks.py`),
   reports progress via SSE, and can be **cancelled** mid-run.
5. Each iteration's outputs are kept in memory until the user clicks
   **Accept** — only on accept are categories persisted to MongoDB.
6. The user can **edit** the resulting categories (rename / merge / delete)
   without re-running AI.
7. Re-running the pipeline against the same scan **replaces** the previous
   category set entirely — no auto-merging, no versioning.
8. A documented hard ceiling on iterations (default: **3**) prevents runaway
   AI cost. If quality isn't acceptable after the cap, the user accepts the
   best iteration produced so far (with a warning) or discards.

## 6. The Iterative Pipeline (3 phases × N iterations)

```
                          ┌──────────────────────────────┐
   START ──────────────▶  │ Phase 1: PROPOSE categories  │
                          │  AI ← all summaries          │
                          │  AI → 5–15 category names    │
                          │       + 1-sentence each      │
                          └────────────┬─────────────────┘
                                       ▼
                          ┌──────────────────────────────┐
                          │ Phase 2: ASSIGN documents    │
                          │  AI ← (category list, batch  │
                          │        of N doc summaries)   │
                          │  AI → doc_id → category_name │
                          └────────────┬─────────────────┘
                                       ▼
                          ┌──────────────────────────────┐
                          │ Phase 3: REVIEW quality      │
                          │  rules detect problems       │
                          │  (singletons, mega, etc.)    │
                          └────────────┬─────────────────┘
                                       │
                       ┌───────────────┴────────────────┐
                       ▼                                ▼
                accept (good)                  iterate (problems found)
                       │                                │
                  ▶ DONE                  AI ← (problems, current
                                              categories, sample docs)
                                          AI → refined category list
                                          → back to Phase 2
```

### 6.1 Phase 1 — Propose

**Input to model:** the configured `system_prompt` (below) + every document's
`title`, `short_summary`, `keywords`, `document_type`. For >300 documents, sample
randomly down to 300 to stay inside a single context window (sampling is
deterministic per scan via a seeded RNG so re-runs are reproducible).

**Prompt sketch:**

```
You are an information architect organizing a collection of {N} documents.
Below are short summaries of each. Propose a flat list of 5–15 mutually
exclusive categories that would best organize this corpus. Each category
should:
  - have a 2–4 word title,
  - have a one-sentence description distinguishing it from the others,
  - apply to at least 3 documents in this corpus.

Return JSON: {"categories": [{"name": "...", "description": "..."}, ...]}
```

**Output validation:** must parse as JSON; must have 5–15 entries; names must
be unique; each must have non-empty `name` and `description`.

### 6.2 Phase 2 — Assign

**Input to model:** the category list + a batch of documents. Default batch
size: **50 documents per call**, parallelism: **2 concurrent calls**.

**Prompt sketch:**

```
You are categorizing documents into one of the categories below. For each
document, pick the single best fit. If none fit well, return "Other".

Categories:
  - Financial Reports: corporate financial statements, earnings, budgets…
  - Product Datasheets: technical product specs and feature sheets…
  - …

Documents (id | title | short summary | keywords):
  ABC123 | Q1 Earnings | … | finance,quarterly,revenue
  …

Return JSON: {"assignments": [{"id": "ABC123", "category": "Financial Reports"}, ...]}
```

**Output validation:** every doc id in the input must appear in the output;
every assigned `category` must be either a proposed name or `"Other"`. Docs
that fail validation (model returned a hallucinated category) are bucketed
into `"Other"` and counted in the quality report.

### 6.3 Phase 3 — Review

Computed **without** an AI call — pure rule evaluation against the assignment:

| Signal | Threshold | What it means |
|---|---|---|
| Singleton categories | any category with count == 1 (or < 1% of docs) | too granular; AI over-proposed |
| Mega-category | any category > 50% of docs | too coarse; AI under-proposed |
| Uncategorized rate | "Other" > 15% of docs | proposed set didn't cover the corpus |
| Imbalance ratio | largest / smallest > 20 | distribution too skewed |

If **any** signal trips, the pipeline iterates (up to the per-run cap). Otherwise
the pipeline stops and presents the accept/edit/discard prompt.

### 6.4 Refinement step (between iterations)

When iterating, **AI is given the problems plus the current state** and asked
for a refined category list:

```
Your previous proposal had these issues:
  - "Niche Research" has only 1 document; merge with a broader category
  - "General" has 62% of documents; split it into 2–4 finer categories

Here are the assignments under the current list (counts per category, and a
few sample summaries from the singleton + mega groups).

Propose a refined flat list of 5–15 categories. Same JSON format as before.
```

Phase 2 then re-runs with the refined list. Phase 3 re-evaluates. The
short-summary list is **the same per-scan sample** used in Phase 1 (so the
model has consistent context across iterations).

## 7. Data Model Changes

### 7.1 MongoDB — `documents` collection

Add two fields to each document (default `None`):

```python
{
    ...,
    "category": Optional[str],          # e.g. "Financial Reports"
    "categorized_at": Optional[datetime],
}
```

Backwards-compatible: existing documents have `None` and are filtered as
"Uncategorized" in the UI.

### 7.2 MongoDB — new `categories` collection

One document per (scan_url, category-set) tuple:

```python
{
    "_id": ObjectId,
    "scan_url": "https://example.com/docs",
    "categories": [
        {"name": "Financial Reports", "description": "…", "count": 54},
        {"name": "Product Datasheets", "description": "…", "count": 48},
        ...
    ],
    "iterations_used": 2,
    "created_at": datetime,
    "model": "gpt-5.4-2026-03-05",      # provenance
    "doc_count_at_creation": 247,
}
```

v1 keeps **one category set per `scan_url`** — a fresh run replaces the previous
document. (v2 could keep history.)

### 7.3 No new SQLite tables

Settings already cover the AI endpoint; no new persisted user prefs needed for v1.

### 7.4 Index

Add a non-text index on `documents.category` (sparse) for the filter dropdown.

## 8. AI Integration Design

### 8.1 Where it lives

New file `app/services/categorization_service.py`. Mirrors the structure of
`ai_summarization_service.py`:

- reuses `_detect_provider`, `_build_request`, `_call_ai_api_once`,
  `_parse_ai_response`, `_redact_url` from the summarization service
  (refactor opportunity: extract those into a shared `app/services/ai_client.py`
  — **propose doing the refactor as part of v1** so the categorization service
  is the smaller, cleaner of the two consumers);
- adds three orchestration coroutines: `propose_categories`, `assign_documents`,
  `refine_categories`;
- adds `run_categorization_pipeline(scan_url, sse_emitter)` that runs the loop.

### 8.2 Token budget guard

Before each AI call, estimate prompt tokens (rough heuristic:
`len(prompt) / 4`). If above a configurable cap (default **120k tokens**),
reduce batch size or sample. Hard refuse the run with a clear error message if
even a single-doc batch exceeds the cap.

### 8.3 Retry / backoff

Inherits the existing summarization retry policy
(`_call_ai_api` → `_call_ai_api_once` with exponential backoff). Phase 2 batches
that exhaust retries bucket their documents into `"Other"` and continue (don't
abort the whole pipeline).

### 8.4 Cost surfaced to user

The progress modal shows running estimates:

- iterations consumed,
- per-iteration AI calls made,
- approximate tokens sent.

## 9. Quality Metrics & Refinement Heuristics

See §6.3 for the rule thresholds. All four numbers (singleton threshold,
mega-category threshold, uncategorized rate, imbalance ratio) and the
per-run iteration cap should be **constants in `categorization_service.py`**
with comments, not user-facing settings — these are tuning knobs the
developer adjusts, not configuration the user changes.

If tuning needs to become user-controllable later, promote to
`runtime_settings`.

## 10. Edge Cases

| Case | Behavior |
|---|---|
| Scan has < 10 summarized documents | Categorize button hidden; tooltip explains "needs at least 10 summarized documents". |
| Scan has only one obvious topic | AI proposes 1–4 categories; Phase 1 validation rejects (< 5); user gets "this corpus appears too homogeneous to categorize meaningfully — try a different scan or wait for more documents". |
| AI returns unparseable JSON | Bubble up as a normal AI error; the iteration is marked failed; user can retry. |
| All AI calls fail | Pipeline aborts with the underlying error; user keeps the previous category set (if any). |
| User cancels mid-run | Partial state discarded; previous category set (if any) preserved. |
| User runs Categorize on a scan that already has categories | Modal warns "This will replace the existing categorization. Continue?" |
| Documents added to the scan after categorization | The new documents have `category=None` until the user re-runs categorization. UI shows them as "Uncategorized". |
| User edits categories then runs Categorize again | Edits are discarded (the AI proposal overwrites). UI warns. |
| AI assigns to a hallucinated category | Bucket into `"Other"`; count in the quality report as a Phase-2 issue (high Other rate triggers refinement). |
| Iteration cap hit without good distribution | UI shows the final state with a warning banner; accept / discard still available. |

## 11. API Surface

All under the existing `/api/download/mongodb` prefix to match the current
MongoDB-related routes:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/mongodb/categorize/start?scan_url=…` | Kick off the pipeline. Returns a session id. |
| GET | `/mongodb/categorize/progress/{session_id}` | SSE: emits `iteration_start`, `phase_progress`, `iteration_complete`, `proposal`, `quality_report`, `final`, `error`. |
| POST | `/mongodb/categorize/accept/{session_id}` | Persist the in-memory categorization to MongoDB. |
| POST | `/mongodb/categorize/cancel/{session_id}` | Cancel a running pipeline. |
| GET | `/mongodb/categories?scan_url=…` | Return the persisted category set + counts. |
| PATCH | `/mongodb/categories?scan_url=…` | Edit categories: `{"action": "rename", ...}` / `"merge"` / `"delete"`. |
| DELETE | `/mongodb/categories?scan_url=…` | Drop the category set entirely (un-categorize all docs). |

Session ids and in-memory progress reuse the existing `session_store` pattern
introduced in Wave 6 (add a third dict, `categorize_sessions`, with the same
TTL sweep).

## 12. UI Changes

### 12.1 Documents page (`app/templates/documents.html`)

- **Summarization bar** gains a "Categorize" button to the right of "Summarize" / "Retry Failed".
- **A new compact "Categories" bar** appears under the summarization bar once a category set exists, showing the categories as clickable filter chips with counts: `[Financial Reports 54] [Product Datasheets 48] …`.
- **Existing extension filter** stays; the category chips act as an additional, AND-combined filter.
- **Each document card / row** gains a small category badge (when categorized).
- **Edit-categories panel** opens from a small ✎ icon next to the Categories bar.

### 12.2 New modal: `categorize.html` partial

The iteration UX from §4.2. Driven by an SSE EventSource. Lives as a partial
template included by `documents.html`, not a new page.

### 12.3 No nav changes

Categorization is a Documents-page action, not a separate top-level page.

## 13. Open Questions (decide before implementation)

| # | Question | Recommendation |
|---|---|---|
| **Q1** | **Per-scan vs cross-scan in v1?** Recommendation: per-scan only; cross-scan is v2. The current Documents page is already scan-scoped — extending to cross-scan would require a new "All scans" view that doesn't exist yet. | Per-scan v1 ✓ |
| **Q2** | **Iteration cap.** Higher cap → better categories on noisy corpora, but more AI cost per run. | Default **3**; expose as a developer constant. |
| **Q3** | **Phase-2 batch size.** Bigger batch → fewer AI calls but larger prompts. | Default **50 docs/batch**, **2 concurrent calls**. |
| **Q4** | **What happens to new documents added after a category set exists?** Options: (a) leave them uncategorized until user re-runs, (b) auto-assign them using the existing category list on next summarization completion, (c) auto-extend the category set. | v1: (a) — keep simple. (b) is a nice v2 follow-up. (c) deferred — risks category drift. |
| **Q5** | **Cost cap per run.** Should we hard-stop if estimated tokens exceed some threshold? | Yes — soft warning at est. 100k tokens, hard refuse at 200k. Surface estimate before starting. |
| **Q6** | **Should the user see the AI prompts?** Useful for debugging, distracting in the happy path. | Hide in normal UI; expose via a "Show diagnostics" toggle in the modal. |
| **Q7** | **Do we want a "Manual seed" option** — user provides a few category names and the AI proposes the rest? Useful when the user already has a mental model. | Defer to v2 unless requested up front. |
| **Q8** | **Refactor: extract shared AI client now or later?** §8.1 proposes pulling `_call_ai_api_once` etc. into `app/services/ai_client.py` as part of v1. | Yes — small, time-bounded refactor; pays off immediately for the second consumer. |
| **Q9** | **Category names: case-insensitive uniqueness?** "Financial Reports" vs "financial reports". | Yes — normalize on `casefold()` for uniqueness checks; preserve user-entered casing for display. |
| **Q10** | **Empty `"Other"` bucket** — show in the Categories bar or hide? | Hide if count == 0; show otherwise. |

## 14. Implementation Milestones

Suggested sequencing — each milestone is reviewable and committable on its own:

1. **M1 — Shared AI client refactor (Q8).** Extract `_call_ai_api_once`,
   `_build_request`, `_parse_ai_response`, `_detect_provider`, `_redact_url`
   into `app/services/ai_client.py`; update `ai_summarization_service.py` to
   import from there. Tests stay green. **~½ day.**
2. **M2 — Data model.** Add `category` + `categorized_at` fields and the index
   migration in `mongodb_service.py`. Add `categories` collection CRUD helpers.
   Unit tests + integration test. **~½ day.**
3. **M3 — Categorization service.** Implement `propose_categories`,
   `assign_documents`, `refine_categories`, `run_categorization_pipeline`. Use
   the shared AI client. Unit tests with mocks + one integration test that
   calls real OpenAI with a fixed corpus of ~20 docs. **~1 day.**
4. **M4 — API surface.** Add the 7 endpoints. SSE plumbing mirrors the existing
   summarization SSE. **~½ day.**
5. **M5 — UI: Categorize modal.** SSE-driven progress, the accept/discard
   buttons. Doesn't yet show categories in the document list. **~½ day.**
6. **M6 — UI: category badges + filter chips + edit panel.** **~½ day.**
7. **M7 — End-to-end polish.** Tooltip on disabled Categorize button, empty
   state copy, cost-estimate display, diagnostics toggle, error handling
   review. **~½ day.**

**Total estimate:** ~3.5 days of focused work for a v1 that runs the full
iteration loop, persists categories, and surfaces them in the UI.

## 15. Out of Scope (explicitly)

- Anything in §3 (Non-goals).
- Server-side concurrency control (single-user utility — at most one
  categorization runs at a time per scan, and even cross-scan concurrency is
  bounded by the existing background-tasks tracker).
- Authn / authz (single-user utility).
- Localization of category names (AI-generated strings, English-only for now).
- Analytics / telemetry on categorization quality across the user's history.

## 16. Acceptance Criteria

V1 is "done" when:

- Categorize button appears under the documented conditions and is hidden
  otherwise.
- Clicking it runs the 3-phase pipeline end-to-end against a real corpus and
  produces a balanced (no signals tripped) result within the iteration cap on
  a "normal" corpus of 100–500 documents.
- The user can Accept, the categories persist to MongoDB, and the badges +
  filter chips show up on the Documents page.
- Re-running on the same scan replaces cleanly.
- Editing (rename/merge/delete) round-trips correctly.
- All existing tests stay green; the new feature has ≥80% line coverage in
  `categorization_service.py` plus at least one integration test against real
  OpenAI + MongoDB.

---

## Resume cues for the next planning session

- Q1–Q10 above need answers (recommendations given; user can override).
- Once answered, the implementation can be tracked as M1–M7 — each milestone
  is small enough to ship as its own commit and reviewable in isolation.
- The shared AI client refactor (M1, also Q8) is the only change that touches
  *existing* code structurally. M2–M7 are additive.
