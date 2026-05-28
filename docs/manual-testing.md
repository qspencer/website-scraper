# Manual Testing Checklist

End-to-end checks for the flows automated tests can't cover (live UI, SSE progress,
Stirling-PDF, MongoDB, and billable AI calls). Run this before considering a change
"done" when it touches scanning, download, summarization, categorization, or startup.

The automated suite (`python -m pytest -q`) covers units and, with `-m integration`,
the real backends. This document is for the human-in-the-loop paths that can't be asserted.

## Prerequisites

- [ ] MongoDB reachable at the URI in Settings (or `mongodb://localhost:27017`).
- [ ] Stirling-PDF container up: `docker ps --filter name=stirling-pdf` shows `Up`.
- [ ] AI configured in **Settings** (API URL, key, model) — needed for summarize/categorize.
- [ ] For JS-heavy sites: `playwright install chromium` has been run in the venv.

---

## 0. Startup (`scripts/run.sh`)

- [ ] **Cold venv / `--force-install`:** `Installing dependencies (first run can take a minute)`
      prints, then a heartbeat dot appears every ~3s, then ` done.` — never a silent gap.
- [ ] **Warm venv:** `Dependencies up to date …` one-liner prints, no dots.
- [ ] **Failed install** (e.g. unreachable index) ends with ` failed — see pip output above.`
      and a non-zero exit, not a hang.
- [ ] Test gate runs and reports pass/fail (or is skipped with `--skip-tests`).
- [ ] Stirling: `Stirling PDF is ready.` or the `Waiting … (up to 180s)` dotted line.
- [ ] **Stirling failure** (port 8080 taken): a `WARNING: failed to start Stirling PDF …`
      prints and the app **still starts** (not aborted by `set -e`).
- [ ] Final banner prints with the PID + URL; `http://127.0.0.1:8000` loads the scan page.

---

## 1. Scanning a website

- [ ] **Single Page Only** scan: progress window shows pages/docs counts via live SSE.
- [ ] **Follow Internal Links** (depth 2): more pages visited; `-`/`+` depth control works.
- [ ] **Batch mode** (Scan-entire-site unchecked): scan pauses; results show
      *"X more pages to scan"* amber banner.
- [ ] **Continue Scanning** adds new docs to existing results; selections preserved.
- [ ] **Autoscan** runs batches automatically; **Stop Autoscan** halts it.
- [ ] **Cancel** mid-scan stops it; docs found so far remain available.
- [ ] Invalid URL shows the right validation message (empty, spaces, no TLD).
- [ ] Recent-URL dropdown appears after a prior scan.

---

## 2. Viewing results

- [ ] **Card view** for ≤50 docs; **Table view** (paginated) for >50.
- [ ] File-type badges colored correctly (PDF red, Word blue, Excel green, …).
- [ ] **Search box** filters as you type; **type filter** narrows to one extension.
- [ ] **Hide inaccessible** (on by default) hides broken links; unchecking shows them.
- [ ] Select / Select All / Deselect All update the selected counter.
- [ ] Scan summary shows URL, pages, docs, time, errors; **Show additional information**
      reveals total/largest/smallest sizes.
- [ ] If errors occurred, **Retry** re-attempts just the failed items.

---

## 3. Download to file system

- [ ] **Download To → File System**; path validation shows green/amber/red as you type.
- [ ] Amber + **Create it** makes a missing folder.
- [ ] **Download** runs; progress window shows per-file complete/failed counts; **Cancel** works.
- [ ] Files land in the chosen folder; duplicate names are de-conflicted.

---

## 4. Store to MongoDB

- [ ] **Download To → MongoDB**; connection status indicator turns green.
- [ ] **Store X files** stores the **selected** count (not the whole corpus — watch the
      progress numerator/denominator match the selection).
- [ ] Text is extracted for PDF/DOCX/XLSX/TXT/PPTX; check a stored doc on the Documents page.
- [ ] **Legacy `.doc`:** a real OLE `.doc` extracts text (needs Stirling/LibreOffice path).
- [ ] **Corrupt PDF / wrong extension:** a misnamed or broken file is detected,
      reclassified to its true format where possible, and doesn't crash the batch.

---

## 5. AI summarization

- [ ] After a MongoDB store completes, summarization starts in the background
      (blue "running in the background" note; dialog closeable).
- [ ] **Documents** page → select scan → status bar shows "X of Y summarized, Z failed"
      **for that scan**, not corpus-wide.
- [ ] **Summarize** appears when some docs are unsummarized; runs only the missing ones.
- [ ] **Retry Failed** resets failures and re-runs; clicking the failure count expands
      per-document error messages.
- [ ] Each summarized doc shows title, short summary, keywords, document-type.

---

## 6. Categorization

- [ ] **Categorize** button enabled only with AI configured **and** ≥10 summarized docs;
      tooltip explains the shortfall otherwise.
- [ ] Iteration loop is visible live: Propose → Assign → Quality check, up to 3 rounds.
- [ ] Final bar chart of categories + counts; if imbalanced, a note explains why and you
      can still **Accept**.
- [ ] **Accept** saves: badges appear, clickable category chips filter the list.
- [ ] **Discard** changes nothing.
- [ ] **✎ Edit:** Rename, Merge (source docs move to target), Delete (docs →
      Uncategorized), Re-run (replaces set), Drop all (set removed).
- [ ] Docs added *after* categorizing show under **Uncategorized** until re-run.
- [ ] Eviction safety: a categorize run abandoned mid-flight (close tab) doesn't keep
      spending AI quota indefinitely (sweeper cancels the task).

---

## 7. Export

- [ ] **Export to CSV** downloads a CSV for the selected scan with title, short summary,
      **category**, source URL, filename, type, version.

---

## 8. Deleting a scan (cascade)

- [ ] **History page** 🗑 and **Documents page** 🗑 **Delete scan** both work; confirm dialog
      states the document count.
- [ ] After delete: the scan's docs, extracted text, summaries, categories, **and** history
      row are all gone.
- [ ] Deletion is **refused** while that scan is being scanned / downloaded / categorized,
      with a "wait" message.
- [ ] **Clear History List** removes history rows only — MongoDB docs remain (verify on
      Documents page).

---

## 9. Settings

- [ ] Changing a value + **Save Settings** persists across a reload.
- [ ] **Test Connection** (MongoDB) reports success/failure accurately.
- [ ] **Reset to Defaults** confirms first, then restores defaults.
- [ ] Crawl depth defaults show 5 / max 10.

---

## 10. Pre-release sanity (quick, automatable)

- [ ] `ruff check app/` → clean.
- [ ] `bandit -r app/` → B110 at 0.
- [ ] `python -m pytest -q` → green; `-m "integration and not live_ai"` → green.
- [ ] No new lines written to the real `logs/scraper.log` by the test run.
