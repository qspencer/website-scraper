# Vision-Capable Document Interpretation — Feature Specification

**Author:** Q. Spencer (with Claude Opus 4.7)
**Date:** 2026-05-26
**Status:** Draft — open questions in §13 should be answered before implementation
**Related:**
  - existing AI summarization (`app/services/ai_summarization_service.py`)
  - existing AI client (`app/services/ai_client.py`)
  - Stirling-PDF OCR fallback (`app/services/stirling_pdf_service.py`)
  - SPEC_CATEGORIZATION_2026-05-20.md (precedent for spec structure)

---

## 1. Problem

Three real document classes the current pipeline can't handle, exposed by the
Railinc scan:

1. **Image-only PDFs** (15 of 792 docs) — scanned pages with no text layer,
   where Stirling-PDF OCR also returns empty (poor scan quality, unusual
   encoding, or genuinely unreadable). Today these end up as
   `text_extraction_status="failed"`, no summary, no category — invisible to
   the user except as the "18 missing AI data" gap in the CSV.
2. **Standalone image files** (`.jpg`, `.png`, `.gif`, etc.) — already
   downloadable when the user picks the "All Files" filter, but have no
   text-extraction path at all. They land in MongoDB with empty `extracted_text`
   and immediately fail summarization.
3. **Embedded figures in text-heavy PDFs** — the text is extracted fine, but
   any chart, diagram, or signed-image content the document depends on is
   ignored. For technical specifications and reports this is a real loss
   ("the schematic on page 4" never makes it into the summary).

Modern multimodal LLMs (GPT-4o, GPT-5.x with vision, Claude 3+ with vision)
can describe rendered images directly. Plumbing a vision-LLM call into the
extraction chain solves all three classes with the same primitive.

## 2. Goals

- Rescue image-only PDFs that Stirling OCR can't read, by rasterizing pages
  and describing them with a vision-LLM.
- Treat standalone image files (`.jpg`, `.png`, `.gif`, `.webp`, `.bmp`,
  `.tiff`) as first-class documents with vision-derived text.
- For text-heavy PDFs, optionally augment the extracted text with descriptions
  of embedded figures, charts, diagrams, and signed-image content.
- Reuse `ai_client.call_chat` plumbing — vision is one more shape of call, not
  a separate stack.
- Defaults that don't surprise the user with cost. Soft-warn and hard-cap on
  estimated cost per call and per scan.

## 3. Non-goals (v1)

- **Real OCR-quality text recovery.** Vision-LLMs paraphrase what they see;
  they don't transcribe verbatim. For documents that need character-perfect
  recovery (legal, regulatory), this feature is a fallback, not a replacement
  for Stirling OCR or a real OCR product.
- **Local vision inference.** No on-device model. Goes through the configured
  AI endpoint, same as text summarization.
- **Video / animated GIF frames.** Static images only.
- **Image search.** The features here produce text descriptions that fold into
  the existing full-text search; no separate vision-similarity index.
- **Per-figure cropping precision.** Embedded-figure handling extracts whole
  embedded images as found; doesn't try to detect figure boundaries within a
  rendered page.
- **Image generation.** Read-only.

## 4. User Experience

Three places the user notices the change:

### 4.1 Settings page

A new section under **AI Summarization** (or as a child of it):

```
AI Vision (optional)
  Vision Model:    [gpt-4o____________________]   (default: blank — falls back to AI Model)
  Page Cap:        [50_____]                       Max PDF pages per vision pass
  Per-Scan Cap:    [$5.00__]                       Refuse vision work past this estimate
  Augment Figures: [ ] When PDF text extraction succeeds, also describe embedded
                       figures and append to extracted text (more expensive)
```

If Vision Model is blank, the existing AI Model is used (assumed to have
vision capability — fails gracefully with a clear error if not). Page Cap and
Per-Scan Cap are hard limits; the user sees a warning if a scan would exceed
them before any vision call fires.

### 4.2 Documents page — extraction transparency

The document detail modal already shows `text_extraction_method`. New possible
values:

| Value | Meaning |
|---|---|
| `vision_pdf` | Image-only PDF rescued by rasterize → vision-LLM |
| `vision_image` | Standalone image file described by vision-LLM |
| `vision_augmented` | Text extracted normally, then embedded figures described and appended |

Card view: a small 👁 icon next to the existing extension badge for
vision-extracted docs (so the user knows the text is LLM-paraphrased, not
verbatim).

### 4.3 Retry Failed bar

When Retry Failed runs on a scan that has image-only PDFs, the progress info
gains a vision phase: e.g., *"15 PDFs: 0 PyPDF2, 12 Stirling fast, 0 OCR, 3
vision (in progress)"*. Cost estimate shown alongside.

### 4.4 What does NOT change

- The Documents page list, search, filter, badges, edit panel — all unchanged.
- Categorization, summarization, CSV export — all unchanged downstream;
  vision-extracted text flows into them via the existing `extracted_text`
  field.
- Local-disk downloads continue to bypass MongoDB and therefore don't trigger
  vision.

## 5. Functional Requirements

1. The feature operates only on documents stored in MongoDB. Files on local
   disk are out of scope (no field to write to).
2. Vision is **opt-in** via a configured Vision Model OR (if blank) inherits
   the AI Model. If the configured model lacks vision, the first call's
   provider error is surfaced clearly and vision is disabled for the rest of
   the scan.
3. PDF rescue triggers only when Stirling OCR has already returned empty —
   it's the bottom of the fallback chain (PyPDF2 → Stirling fast →
   Stirling OCR → vision).
4. Standalone image extraction triggers on `.jpg/.jpeg/.png/.gif/.webp/.bmp/.tiff`.
5. Embedded-figure augmentation triggers only when the user has explicitly
   enabled it in Settings (default off).
6. Every vision call respects the **per-call HARD_TOKEN_CAP** (mirroring
   `categorization_service`) and the per-scan budget cap.
7. Cost estimates are computed and surfaced **before** the call fires.
   Estimate basis: image-encoded-token cost from the provider's published
   per-image rate.
8. Vision-extracted text is treated identically to OCR'd text by downstream
   summarization, categorization, search, and CSV export.

## 6. The Three Use Cases in Detail

### 6.1 Image-PDF rescue

```
PDF bytes → PyPDF2.extract_text → empty?
                                    ↓ yes
              → Stirling fast (PDF-to-text) → empty?
                                    ↓ yes
              → Stirling OCR (force-OCR) → empty?
                                    ↓ yes
              → Vision rescue:
                    rasterize PDF pages → N images
                    cap at PAGE_CAP pages (default 50)
                    for each page (or batched):
                        ai_client.call_chat_vision(
                            system=VISION_PAGE_PROMPT,
                            user=[image bytes + page index],
                        )
                    concatenate page descriptions with page markers
              → text_extraction_method = "vision_pdf"
```

**Rasterization:** library choice — `pypdfium2` (lightweight, no system deps,
pure-Python wrapper around PDFium) over `pdf2image` (needs `poppler-utils`
installed system-wide). pypdfium2 is already on PyPI, single-wheel, no extra
deps; recommend adopting it for v1.

**Page batching:** GPT-5.x and Claude both accept up to ~20 images per call.
For a 10-page PDF, one call. For a 60-page PDF, three calls. Default
PAGE_CAP=50 to keep cost predictable.

**Prompt sketch:**

```
You are transcribing a scanned document for a corpus indexer. The image
contains one rendered page of a PDF. Produce the visible text content as
faithfully as possible: preserve headings, lists, table structures, and
paragraph breaks. Do not paraphrase or summarize. If a region is illegible,
mark it with [illegible].
```

### 6.2 Standalone image extraction

```
.jpg/.png/.gif/etc. → ai_client.call_chat_vision(
                          system=VISION_IMAGE_PROMPT,
                          user=[image bytes],
                      )
                   → text_extraction_method = "vision_image"
```

**Prompt sketch:**

```
You are describing an image for a document indexer. Describe in detail:
  - any text visible in the image (verbatim where possible),
  - the overall subject matter,
  - any tables, charts, diagrams, logos, or structured content.
Return plain text; do not invent context not visible in the image.
```

**Image preprocessing:** very large images (> 4 MP) are downscaled before
sending (most providers do this server-side anyway; doing it locally saves
upload bandwidth). Skip vision entirely for tiny images (< 10 KB) — they're
probably decorative spacers, icons, or favicons.

### 6.3 Embedded-figure augmentation (opt-in)

```
PDF bytes → PyPDF2 → text (non-empty, normal extraction)
         → pypdfium2 / pdfimages → extract embedded images
         → filter:  drop images < threshold size (decorative)
                    drop duplicates (logos repeated across pages)
         → for each remaining figure:
               ai_client.call_chat_vision(
                   system=VISION_FIGURE_PROMPT,
                   user=[image bytes],
               )
         → append "\n\n[Figure on page N]: {description}" to extracted text
         → text_extraction_method = "vision_augmented"
```

**Prompt sketch:**

```
You are describing a figure or diagram extracted from a document. In 2–4
sentences, describe what is shown: the type (chart, schematic, photo,
illustration), the subject, and any key labels or numbers visible. Do not
speculate about context outside what is visible.
```

**Default off** — too easy to rack up cost on PDF-heavy scans. Enable per
Settings (toggle) or per individual document (future). The "All scans get
augmented" mode is intentionally not the default.

## 7. Data Model Changes

### 7.1 MongoDB — `documents` collection

No new fields. The existing `text_extraction_method` field gains three new
possible values (see §4.2). The `extracted_text` field continues to hold
either real text, OCR text, or vision-derived text — downstream code doesn't
distinguish.

### 7.2 Optional: per-document vision metadata

For transparency, *consider* adding (open question Q4):

```python
{
    ...,
    "vision_extraction": {
        "model": "gpt-4o-2024-08-06",
        "page_count": 12,
        "estimated_cost_usd": 0.18,
        "augment_figures": False,
    }
}
```

This would let the detail modal show "Vision: 12 pages, ~$0.18" and let the
user audit cost retrospectively. Defer to Q4.

### 7.3 Runtime settings

New persisted settings (alongside existing `ai_*` family):

```python
ai_vision_model: str = ""           # blank → inherit ai_model
ai_vision_page_cap: int = 50        # max pages per PDF
ai_vision_scan_cap_usd: float = 5.0 # refuse work past this estimate per scan
ai_vision_augment_figures: bool = False  # opt-in figure augmentation
```

## 8. AI Integration Design

### 8.1 Where it lives

Extend `app/services/ai_client.py` with a vision primitive:

```python
async def call_chat_vision(
    system_prompt: str,
    user_prompt: str,
    images: list[bytes],            # raw bytes; encoded to base64 per provider
    *, label: str = "",
    max_tokens: int = 2048,
) -> dict:
    """Send images + prompts to the configured vision model.

    Returns {"raw_text": str} on success or {"error": str}. Mirrors the
    contract of call_chat — caller is responsible for parsing.
    """
```

Provider request shapes:

- **OpenAI-compatible**: `messages: [{"role":"user","content":[{"type":"text","text":...}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]}]`
- **Anthropic**: `messages: [{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}, {"type":"text","text":...}]}]`

Provider detection reuses `ai_client.detect_provider`.

### 8.2 New service module

`app/services/vision_service.py` orchestrates the three use cases. Mirrors the
shape of `categorization_service` — pure functions for each phase, no global
state, deterministic where possible (image hashing for dedup of embedded
figures).

Functions:

- `extract_text_from_image_pdf(file_data, page_cap) -> Optional[str]`
- `extract_text_from_image(file_data, content_type) -> Optional[str]`
- `augment_text_with_figure_descriptions(file_data, existing_text) -> str`

### 8.3 Integration into the extraction chain

`mongodb_service.retry_text_extraction` already has the PyPDF2 → Stirling-fast
→ Stirling-OCR cascade for PDFs. Add a fourth step:

```python
# (existing) try standard extract → try Stirling fast → try Stirling OCR
# (new) if still failed and ext == ".pdf" and vision_configured:
ocr_text = stirling_pdf_service.extract_text_via_ocr(file_data)
if ocr_text:
    ...
else:
    vision_text = vision_service.extract_text_from_image_pdf(file_data, ...)
    if vision_text:
        text = vision_text
        status = "complete"
        method = "vision_pdf"
        stats["vision_pdf"] += 1
```

For standalone images, extend the `extractors` dict in `text_extraction_service`:

```python
extractors = {
    ...,
    "jpg":  _extract_image,
    "jpeg": _extract_image,
    "png":  _extract_image,
    ...
}

def _extract_image(file_data: bytes) -> str:
    from app.services import vision_service
    text = vision_service.extract_text_from_image(file_data, "image/png")
    if text is None:
        raise RuntimeError("Vision extraction failed; AI not configured or hit cap")
    return text
```

For embedded-figure augmentation, run as a post-step after successful normal
text extraction, gated by `runtime_settings.ai_vision_augment_figures`.

### 8.4 Cost estimation

Per-image-token cost varies by provider. Rough heuristics (verify against
current pricing at implementation time):

- GPT-4o / GPT-5.x: ~85 tokens base + (170 × ceil(image_width/512) × ceil(image_height/512)) detail tokens
- Claude 3+: roughly tile-based, similar magnitude

Pre-call estimator:

```python
def estimate_image_tokens(width: int, height: int) -> int:
    # OpenAI's high-detail formula, conservative
    tiles_w = (width + 511) // 512
    tiles_h = (height + 511) // 512
    return 85 + 170 * tiles_w * tiles_h
```

Multiply by current per-token price, sum across pages, refuse work past the
per-scan cap.

## 9. Edge Cases

| Case | Behavior |
|---|---|
| Vision Model not configured AND AI Model lacks vision | First vision call fails with provider error; surface clearly in `text_extraction_error`; mark `text_extraction_status="failed"` (don't keep retrying). |
| Per-scan cost cap would be exceeded mid-run | Process docs in cheapest-first order (small images first); when cap would be exceeded, stop and report what completed. Already-spent cost is sunk. |
| PDF has 200 pages but PAGE_CAP=50 | Rasterize first 50 pages only; append note `[Note: vision processed first 50 of 200 pages]` to the extracted text so downstream summarization understands the truncation. |
| Image too small to be meaningful (< 10 KB) | Skip vision entirely; mark `text_extraction_status="unsupported"` with message "Image too small for vision extraction". |
| Animated GIF | Use first frame only. Don't try to describe motion. |
| Image is corrupted / not decodable | Standard error path: `text_extraction_error="Image decode failed: ..."`, status="failed". |
| Vision returns nonsense / refusal | Treat as empty result, same as Stirling OCR returning empty. Falls into normal failure path. |
| User has Augment Figures on but the PDF has zero extractable images | No-op; text extraction completes as normal with `method="standard"`, not `"vision_augmented"`. |
| Embedded figure is a giant page-sized image (the entire page IS the figure) | Skip during augmentation (treat as already-handled by the page rasterization path). Detection: image dims approximately match page dims. |
| Vision API rate-limited mid-scan | Inherits `ai_client.call_chat`'s retry/backoff. After max retries, that doc fails; rest of scan continues. |

## 10. API Surface

No new endpoints. Vision is plumbed into the existing extraction chain via
the existing `Retry Failed` button and the implicit `retry_text_extraction`
inside `summarize_pending_documents`.

Optional v1.1: `POST /api/download/mongodb/document/{id}/vision-retry` — a
single-document re-extract that forces the vision path (for the user who
wants to selectively recover a specific failed doc without re-running the
whole batch). Defer to Q5.

## 11. UI Changes

Per §4. Specifically:

- **Settings page** gains an "AI Vision (optional)" subsection — 4 fields.
- **Document detail modal** displays the new `text_extraction_method` values
  with human-readable labels (e.g., "Vision (image-PDF rescue)").
- **Document card** gets a 👁 badge next to the file-type badge for any doc
  whose `text_extraction_method` starts with `vision_`.
- **Retry Failed progress** display adds a "vision" line item.

No changes to: Documents page chrome, Categories bar, search/filter,
chips, edit panel.

## 12. (Reserved — duplicate of §13 below)

## 13. Open Questions (decide before implementation)

| # | Question | Recommendation |
|---|---|---|
| **Q1** | **Default Vision Model**. Leave blank → inherits AI Model? Or default to a specific known-vision model like `gpt-4o`? | Blank with a tooltip explaining that "current AI model must support vision; otherwise set explicitly." Less magic. |
| **Q2** | **Page batching strategy** for image-PDF rescue: one call per page, or batch up to N pages per call? Bigger batches = fewer calls but worse per-page faithfulness (model context dilutes). | Default **5 pages per call**. Per-call cost predictable; per-page quality acceptable. |
| **Q3** | **Per-scan cost cap default**. $5? $10? Make it currency-explicit so the user sees real money. | **$5.00 USD** default. Generous enough for most scans, low enough to surface a confirmation prompt for batch-of-1000 image PDFs. |
| **Q4** | **Per-document vision metadata field** (§7.2). Adds insight but bloats the documents collection. | Skip in v1. Aggregate stats via search; per-doc only if a clear use case emerges. |
| **Q5** | **Per-document vision-retry endpoint** (§10). | Skip in v1. User can use Retry Failed at scan level. |
| **Q6** | **Embedded-figure augmentation default**. Off (user opts in) or on (and watch the bill)? | **Off**. Augmentation is high-cost and low-frequency-of-need; opt-in. |
| **Q7** | **Library for PDF rasterization**: `pypdfium2` (pure Python wheel, no system deps) or `pdf2image` (needs poppler installed)? | **pypdfium2** — no system dep, single pip install, matches the project's "easy local utility" framing. |
| **Q8** | **Should vision augmentation also fire for the standalone image case**? E.g., when a `.jpg` is described, also try to find/describe sub-images within it. | No. Don't recurse. |
| **Q9** | **Cost display unit**: tokens or USD? USD requires per-model pricing constants that go stale. | Show **both** — "≈ 14,000 vision tokens (≈ $0.21 at current OpenAI rates, see config)". Token count is authoritative; USD is best-effort. |
| **Q10** | **Image dedup across embedded figures** — if a logo appears on every page, describe once or every time? | Hash-dedup; describe once; reference the cached description by hash for repeats. |

## 14. Implementation Milestones

1. **M1 — `ai_client.call_chat_vision`.** Provider-aware request body, base64
   image encoding, error handling. Unit tests with mocked vision responses.
   **~½ day.**
2. **M2 — `vision_service`** with the three orchestration functions (PDF
   rescue, image, augment). Token estimator. Cost cap guard. **~1 day.**
3. **M3 — Integration into `retry_text_extraction` and `text_extraction_service`.**
   New extractor for image extensions; new vision branch after Stirling OCR
   in the PDF cascade. **~½ day.**
4. **M4 — Settings UI.** Four new fields + plumbing. **~½ day.**
5. **M5 — Document badge + detail-modal extraction-method labels.** **~¼ day.**
6. **M6 — Optional: embedded-figure augmentation path.** Smaller in
   isolation than (1)+(2); can be its own milestone or folded into M2/M3.
   **~½ day.**
7. **M7 — Tests + live verification.** Unit tests with mocked vision; live
   integration test against real vision API on a small fixture corpus
   (e.g., 1 image-PDF + 1 standalone JPG + 1 text-PDF with augmentation).
   **~½ day.**

**Total estimate:** ~3.5 days for the full feature.

## 15. Out of Scope (explicitly)

- Anything in §3 (Non-goals).
- Adapting categorization to use image content directly (vision-derived text
  flows into the existing pipeline; that's the integration).
- Caching vision responses across re-runs (each re-run re-pays cost; user is
  responsible for not running Retry Failed in a loop).
- Vision for HTML / DOCX / XLSX (those formats already extract structured
  text well; vision adds cost without value).
- Multilingual prompting. English prompts only in v1; the model handles
  multilingual *content* fine.

## 16. Acceptance Criteria

V1 is "done" when:

- Vision Model field is in Settings; setting it enables vision.
- Running Retry Failed on a scan with image-only PDFs that have failed
  Stirling OCR now produces non-empty extracted text via vision, with
  `text_extraction_method="vision_pdf"`, on at least 80% of the test
  fixture set.
- Storing a `.jpg`/`.png` file to MongoDB produces vision-derived text and
  flows into summarization and categorization without manual intervention.
- Cost caps are enforced: a synthetic large-PDF test that would exceed
  PER_SCAN_CAP_USD aborts cleanly with a clear error, not silently spends.
- All existing tests stay green; new feature has ≥80% line coverage in
  `vision_service.py` plus a live integration test that round-trips one of
  each use case against real OpenAI.

---

## Resume cues for the next planning session

- Q1–Q10 above need answers (recommendations given; user can override).
- Once answered, implementation tracked as M1–M7 — each milestone small enough
  to ship as its own commit, similar to the categorization rollout.
- The natural first concrete validation is the Railinc scan's 15 OCR-resistant
  PDFs — if vision rescues even half of them, the feature pays for itself
  immediately for that corpus.
