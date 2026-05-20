"""Iterative AI-driven document categorization (see SPEC_CATEGORIZATION_2026-05-20.md).

The pipeline runs per scan_url and consists of three phases:

  Phase 1 (PROPOSE)   AI proposes 5–15 categories from a sample of summaries.
  Phase 2 (ASSIGN)    AI assigns every summarized doc to one category (or "Other").
  Phase 3 (REVIEW)    Rule-based quality check looking for singletons, mega-
                      categories, high "Other" rate, or extreme imbalance.

  If the quality check trips any signal, the loop iterates with a REFINE step
  (give the model the issues + sample assignments, ask for a refined list)
  until quality is acceptable or the iteration cap is hit.

Prompt-injection note (cf. ai_summarization_service): the document summaries
fed into Phase 1 / Phase 2 are themselves LLM output produced from scraped
content, so they are untrusted twice over. Categories returned must not be
used to drive automation; they're presented to the user as a UI affordance
only.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.core.logging_config import get_logger
from app.services import ai_client, mongodb_service
from app.services.settings_service import runtime_settings

logger = get_logger(__name__)


# --- Tunables --------------------------------------------------------------
# These are developer constants by design (per spec §9). Promote to
# runtime_settings only if user-facing control becomes necessary.

ITERATION_CAP = 3                    # max passes through propose/assign before giving up
PHASE1_SAMPLE_SIZE = 300             # cap on summaries sent to PROPOSE / REFINE
PHASE2_BATCH_SIZE = 50               # docs per ASSIGN call
PHASE2_CONCURRENCY = 2               # simultaneous ASSIGN calls
SOFT_TOKEN_WARN = 100_000            # log warning if a call exceeds this
HARD_TOKEN_CAP = 200_000             # refuse a call above this (catches runaway prompts)

# Quality thresholds — when any of these trips, the pipeline iterates.
SINGLETON_FRACTION = 0.01            # categories below this share of docs are "too small"
MEGA_FRACTION = 0.50                 # categories above this share are "too large"
OTHER_FRACTION = 0.15                # "Other" above this means the proposed set didn't cover the corpus
IMBALANCE_RATIO = 20                 # largest/smallest > this is too skewed

# Minimum corpus size — fewer than this and categorization isn't meaningful.
MIN_SUMMARIZED_DOCS = 10

# Validation bounds on the proposed category set (matches what the prompt asks for).
MIN_CATEGORIES = 5
MAX_CATEGORIES = 15


# --- Errors ----------------------------------------------------------------


class CategorizationError(RuntimeError):
    """Raised when the pipeline cannot proceed (AI not configured, bad output, etc.)."""


# --- Prompts ---------------------------------------------------------------


PROPOSE_SYSTEM_PROMPT = (
    "You are an information architect organizing a collection of documents. "
    "Given short summaries of each document, propose a flat list of "
    f"{MIN_CATEGORIES}–{MAX_CATEGORIES} mutually exclusive categories that would best "
    "organize the corpus. Each category must:\n"
    "  - have a 2–4 word title,\n"
    "  - have a one-sentence description distinguishing it from the others,\n"
    "  - plausibly apply to at least 3 documents.\n\n"
    'Return ONLY valid JSON of the form {"categories": [{"name": "...", "description": "..."}, ...]} '
    "with no markdown fencing and no extra text."
)

ASSIGN_SYSTEM_PROMPT = (
    "You are categorizing documents into one of the provided categories. For each document, "
    "pick the single best fit by category name. If none of the categories fit reasonably, "
    'return "Other".\n\n'
    'Return ONLY valid JSON of the form {"assignments": [{"id": "...", "category": "..."}, ...]} '
    "with no markdown fencing and no extra text. Every input document id must appear exactly "
    "once in the output."
)

REFINE_SYSTEM_PROMPT = (
    "You are refining a list of document categories. The previous proposal had quality issues "
    "summarized below. Propose a refined flat list of "
    f"{MIN_CATEGORIES}–{MAX_CATEGORIES} mutually exclusive categories that addresses these "
    "issues. Same rules as before: 2–4 word title, one-sentence description, applies to at "
    "least 3 documents.\n\n"
    'Return ONLY valid JSON of the form {"categories": [{"name": "...", "description": "..."}, ...]} '
    "with no markdown fencing and no extra text."
)


# --- Helpers ---------------------------------------------------------------


# An emit() callable used to push SSE events. Pipeline callers pass a function
# matching this signature; the service calls emit(event_type, payload_dict).
EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]


async def _noop_emit(event_type: str, payload: Dict[str, Any]) -> None:
    """Default emitter that drops events on the floor."""


def _ai_configured() -> bool:
    return bool(
        runtime_settings.ai_api_url
        and runtime_settings.ai_api_key
        and runtime_settings.ai_model
    )


def _estimate_tokens(s: str) -> int:
    """Quick prompt-length estimate (~4 chars per token; close enough for guardrails)."""
    return (len(s) + 3) // 4


def _check_token_budget(label: str, prompt: str) -> None:
    """Warn / refuse based on prompt size. Raises CategorizationError on hard cap."""
    est = _estimate_tokens(prompt)
    if est > HARD_TOKEN_CAP:
        raise CategorizationError(
            f"Prompt for {label} ({est} estimated tokens) exceeds hard cap of "
            f"{HARD_TOKEN_CAP}. Reduce sample size or batch size."
        )
    if est > SOFT_TOKEN_WARN:
        logger.warning(f"Prompt for {label} is large: ~{est} tokens (soft warn at {SOFT_TOKEN_WARN})")


def _seeded_sample(items: List[Any], n: int, seed_key: str) -> List[Any]:
    """Deterministically sample n items using a hash of seed_key as the RNG seed.

    Re-running with the same seed_key returns the same sample, which is what
    refinement iterations want: the model sees a consistent corpus snapshot.
    """
    if len(items) <= n:
        return list(items)
    rng = random.Random(hash(seed_key) & 0xFFFFFFFF)
    return rng.sample(items, n)


def _summary_line(doc: Dict[str, Any]) -> str:
    """One compact line representing a document in a prompt body."""
    title = (doc.get("title") or doc.get("filename") or "").strip().replace("|", "/")
    short = (doc.get("short_summary") or "").strip().replace("\n", " ").replace("|", "/")
    keywords = doc.get("keywords") or []
    kw = ",".join(str(k) for k in keywords[:6])
    dt = (doc.get("document_type") or "").strip()
    return f"{doc['_id']} | {title} | {short[:200]} | {kw} | {dt}"


def _parse_categories_json(raw: str) -> List[Dict[str, str]]:
    """Parse a {'categories': [...]} response. Raises CategorizationError on bad shape.

    Strips markdown fences if present (some models add them despite instructions).
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CategorizationError(f"AI returned unparseable JSON: {e}; raw start: {raw[:200]}")
    if not isinstance(data, dict) or "categories" not in data or not isinstance(data["categories"], list):
        raise CategorizationError(f"AI response missing 'categories' list: {str(data)[:200]}")

    cats: List[Dict[str, str]] = []
    seen_names = set()
    for entry in data["categories"]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        desc = str(entry.get("description", "")).strip()
        if not name or not desc:
            continue
        key = name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        cats.append({"name": name, "description": desc})

    if not (MIN_CATEGORIES <= len(cats) <= MAX_CATEGORIES):
        raise CategorizationError(
            f"AI proposed {len(cats)} categories; expected {MIN_CATEGORIES}–{MAX_CATEGORIES}"
        )
    return cats


def _parse_assignments_json(raw: str, expected_ids: List[str], valid_names: List[str]) -> Dict[str, str]:
    """Parse a {'assignments': [...]} response.

    Hallucinated category names (not in valid_names + 'Other') are coerced to 'Other'.
    Missing ids are also coerced to 'Other'. Returns {doc_id: category}.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CategorizationError(f"AI returned unparseable JSON: {e}; raw start: {raw[:200]}")
    if not isinstance(data, dict) or "assignments" not in data:
        raise CategorizationError(f"AI response missing 'assignments' list: {str(data)[:200]}")

    valid = {n.casefold(): n for n in valid_names}
    valid["other"] = "Other"

    by_id: Dict[str, str] = {}
    for entry in data.get("assignments", []):
        if not isinstance(entry, dict):
            continue
        doc_id = str(entry.get("id", "")).strip()
        cat = str(entry.get("category", "")).strip()
        if not doc_id:
            continue
        canonical = valid.get(cat.casefold(), "Other")
        by_id[doc_id] = canonical

    # Any expected id the model omitted gets bucketed into "Other"
    for doc_id in expected_ids:
        by_id.setdefault(doc_id, "Other")
    return by_id


# --- Phase 1: PROPOSE ------------------------------------------------------


async def propose_categories(docs: List[Dict[str, Any]], seed_key: str) -> List[Dict[str, str]]:
    """Phase 1. Ask the AI to propose 5–15 categories from a sample of docs.

    Returns [{'name': ..., 'description': ...}, ...]. Raises CategorizationError
    on bad output or transport errors.
    """
    sample = _seeded_sample(docs, PHASE1_SAMPLE_SIZE, seed_key)
    body = "\n".join(_summary_line(d) for d in sample)
    user_prompt = (
        f"There are {len(docs)} documents in this corpus"
        + (f" (showing a representative sample of {len(sample)})" if len(sample) < len(docs) else "")
        + ".\nFields: id | title | short summary | keywords | document_type\n\n"
        + body
    )
    _check_token_budget("propose", user_prompt)

    result = await ai_client.call_chat(PROPOSE_SYSTEM_PROMPT, user_prompt, label="propose", max_tokens=2048)
    if "error" in result:
        raise CategorizationError(f"PROPOSE call failed: {result['error']}")
    return _parse_categories_json(result["raw_text"])


# --- Phase 2: ASSIGN -------------------------------------------------------


def _assign_user_prompt(categories: List[Dict[str, str]], batch: List[Dict[str, Any]]) -> str:
    cat_lines = "\n".join(f"  - {c['name']}: {c['description']}" for c in categories)
    doc_lines = "\n".join(_summary_line(d) for d in batch)
    return (
        f"Categories:\n{cat_lines}\n\n"
        f"Documents (id | title | short summary | keywords | document_type):\n{doc_lines}"
    )


async def _assign_one_batch(
    categories: List[Dict[str, str]], batch: List[Dict[str, Any]],
) -> Dict[str, str]:
    """One ASSIGN call against one batch. Returns {doc_id: category_name}.

    On any failure (AI error, parse error), buckets every doc in the batch into
    "Other" so the pipeline can still complete. The high resulting "Other" rate
    will trigger refinement in Phase 3.
    """
    valid_names = [c["name"] for c in categories]
    expected_ids = [d["_id"] for d in batch]
    user_prompt = _assign_user_prompt(categories, batch)
    _check_token_budget("assign", user_prompt)

    result = await ai_client.call_chat(
        ASSIGN_SYSTEM_PROMPT, user_prompt, label="assign", max_tokens=4096,
    )
    if "error" in result:
        logger.warning(f"ASSIGN batch failed (bucketing into Other): {result['error']}")
        return {doc_id: "Other" for doc_id in expected_ids}
    try:
        return _parse_assignments_json(result["raw_text"], expected_ids, valid_names)
    except CategorizationError as e:
        logger.warning(f"ASSIGN parse failed (bucketing into Other): {e}")
        return {doc_id: "Other" for doc_id in expected_ids}


async def assign_documents(
    docs: List[Dict[str, Any]],
    categories: List[Dict[str, str]],
    emit: Optional[EmitFn] = None,
) -> Dict[str, str]:
    """Phase 2. Batch all docs and run ASSIGN with bounded concurrency.

    Returns {doc_id: category_name}. Hallucinations/missing ids fall into "Other".
    """
    emit = emit or _noop_emit
    batches = [docs[i:i + PHASE2_BATCH_SIZE] for i in range(0, len(docs), PHASE2_BATCH_SIZE)]
    total = len(batches)
    semaphore = asyncio.Semaphore(PHASE2_CONCURRENCY)
    completed = 0
    results: Dict[str, str] = {}
    lock = asyncio.Lock()

    async def _runner(batch_idx: int, batch: List[Dict[str, Any]]) -> None:
        nonlocal completed
        async with semaphore:
            partial = await _assign_one_batch(categories, batch)
        async with lock:
            results.update(partial)
            completed += 1
            await emit("phase_progress", {
                "phase": "assign", "completed": completed, "total": total,
            })

    await asyncio.gather(*(_runner(i, b) for i, b in enumerate(batches)))
    return results


# --- Phase 3: REVIEW (pure rules) ------------------------------------------


def evaluate_quality(
    assignments: Dict[str, str], categories: List[Dict[str, str]], total_docs: int,
) -> Dict[str, Any]:
    """Pure rule check. Returns:

        {
            "ok": bool,
            "signals": ["singleton: X", "mega: Y", ...],
            "distribution": {"Cat A": 12, "Cat B": 4, "Other": 7, ...},
            "other_count": int,
        }
    """
    distribution: Dict[str, int] = {c["name"]: 0 for c in categories}
    distribution["Other"] = 0
    for cat in assignments.values():
        distribution[cat] = distribution.get(cat, 0) + 1

    signals: List[str] = []
    if total_docs == 0:
        return {"ok": False, "signals": ["no documents"], "distribution": distribution, "other_count": 0}

    other_count = distribution.get("Other", 0)
    non_other = {k: v for k, v in distribution.items() if k != "Other"}

    # Singletons / too-small categories
    for name, count in non_other.items():
        if count == 1 or (count > 0 and count / total_docs < SINGLETON_FRACTION):
            signals.append(f"singleton: {name!r} has {count} doc(s)")

    # Mega categories
    for name, count in non_other.items():
        if count / total_docs > MEGA_FRACTION:
            signals.append(f"mega: {name!r} holds {count}/{total_docs} ({100*count/total_docs:.0f}%)")

    # High Other rate
    if other_count / total_docs > OTHER_FRACTION:
        signals.append(f"uncategorized: Other holds {other_count}/{total_docs} "
                       f"({100*other_count/total_docs:.0f}%)")

    # Imbalance ratio among non-empty non-Other buckets
    populated = [v for v in non_other.values() if v > 0]
    if populated:
        ratio = max(populated) / min(populated)
        if ratio > IMBALANCE_RATIO:
            signals.append(f"imbalance: largest/smallest = {ratio:.1f}")

    return {
        "ok": not signals,
        "signals": signals,
        "distribution": distribution,
        "other_count": other_count,
    }


# --- Refinement ------------------------------------------------------------


def _refine_user_prompt(
    current: List[Dict[str, str]], quality: Dict[str, Any], sample: List[Dict[str, Any]],
) -> str:
    issues = "\n".join(f"  - {s}" for s in quality["signals"])
    dist_lines = "\n".join(
        f"  - {name}: {count}" for name, count in sorted(
            quality["distribution"].items(), key=lambda kv: kv[1], reverse=True,
        )
    )
    cat_lines = "\n".join(f"  - {c['name']}: {c['description']}" for c in current)
    sample_lines = "\n".join(_summary_line(d) for d in sample)
    return (
        f"Previous proposal:\n{cat_lines}\n\n"
        f"Issues with that proposal:\n{issues}\n\n"
        f"Distribution under that proposal:\n{dist_lines}\n\n"
        f"Representative sample of documents (id | title | short summary | keywords | document_type):\n"
        f"{sample_lines}"
    )


async def refine_categories(
    current: List[Dict[str, str]],
    quality: Dict[str, Any],
    docs: List[Dict[str, Any]],
    seed_key: str,
) -> List[Dict[str, str]]:
    """Ask the AI for a refined category list addressing the quality issues."""
    sample = _seeded_sample(docs, PHASE1_SAMPLE_SIZE, seed_key)
    user_prompt = _refine_user_prompt(current, quality, sample)
    _check_token_budget("refine", user_prompt)
    result = await ai_client.call_chat(REFINE_SYSTEM_PROMPT, user_prompt, label="refine", max_tokens=2048)
    if "error" in result:
        raise CategorizationError(f"REFINE call failed: {result['error']}")
    return _parse_categories_json(result["raw_text"])


# --- Orchestrator ----------------------------------------------------------


async def run_categorization_pipeline(
    scan_url: str, emit: Optional[EmitFn] = None,
) -> Dict[str, Any]:
    """Run the full propose → assign → review loop for ``scan_url``.

    Returns a dict suitable for handing to mongodb_service.accept_categorization:

        {
            "scan_url": str,
            "categories": [...],          # final list (name + description, no count)
            "assignments": {doc_id: cat}, # final assignment map
            "quality": {ok, signals, distribution, other_count},
            "iterations_used": int,
            "model": str,
            "doc_count": int,
        }

    Raises CategorizationError if the pipeline cannot complete (AI not configured,
    not enough docs, or all attempts produce unparseable output).
    """
    emit = emit or _noop_emit

    if not _ai_configured():
        raise CategorizationError("AI API not configured (missing URL, key, or model)")

    docs = mongodb_service.get_summarized_documents_for_scan(scan_url)
    if len(docs) < MIN_SUMMARIZED_DOCS:
        raise CategorizationError(
            f"Need at least {MIN_SUMMARIZED_DOCS} summarized documents to categorize; "
            f"this scan has {len(docs)}."
        )

    model = runtime_settings.ai_model
    best: Optional[Dict[str, Any]] = None  # best iteration so far (in case we hit cap)

    for iteration in range(1, ITERATION_CAP + 1):
        await emit("iteration_start", {"iteration": iteration, "cap": ITERATION_CAP})

        if iteration == 1:
            categories = await propose_categories(docs, seed_key=scan_url)
        else:
            categories = await refine_categories(
                current=best["categories"], quality=best["quality"], docs=docs, seed_key=scan_url,
            )

        await emit("proposal", {"iteration": iteration, "categories": categories})

        assignments = await assign_documents(docs, categories, emit=emit)
        quality = evaluate_quality(assignments, categories, total_docs=len(docs))

        await emit("quality_report", {"iteration": iteration, "quality": quality})

        current = {
            "categories": categories,
            "assignments": assignments,
            "quality": quality,
        }
        # Keep the best iteration: prefer ok=True; otherwise prefer fewer signals
        # then lower Other count, as a stable tiebreaker.
        if best is None:
            best = current
        else:
            curr_score = (not quality["ok"], len(quality["signals"]), quality["other_count"])
            best_score = (not best["quality"]["ok"], len(best["quality"]["signals"]), best["quality"]["other_count"])
            if curr_score < best_score:
                best = current

        if quality["ok"]:
            logger.info(f"Categorization converged at iteration {iteration}")
            break
    else:
        logger.warning(f"Categorization hit iteration cap ({ITERATION_CAP}); returning best result")

    assert best is not None  # loop runs at least once
    iterations_used = iteration  # last iteration that ran

    final = {
        "scan_url": scan_url,
        "categories": best["categories"],
        "assignments": best["assignments"],
        "quality": best["quality"],
        "iterations_used": iterations_used,
        "model": model,
        "doc_count": len(docs),
    }
    await emit("final", final)
    return final
