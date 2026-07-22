# Synchronous math-normalization at annotation intake

> Tier: implementation-adjacent
> Parent plan: root / user-facing
> Externalized fit: private — two feature branches in scope (h fork, client fork).
> Status: **historical design record.** The h/client work it scopes is implemented on the
>   fork branches (h PR #1, client PR #2), and the hypothesis-review CLI rework it deferred
>   ("separate later task") has since landed on this branch (PR #8): marker mechanism
>   dropped for time-window sessions, `normalized_quote` consumed from h,
>   `mathquote.py`/`pdfmath.py`/`enrich.py` retired. The invariants below (backend-owned
>   normalization, never-raw display, fail-hard) remain the live contract; the scoping
>   notes are kept as written for provenance.

## Purpose / Observable Result
- A researcher annotates rendered math (an arXiv PDF region, or a KaTeX/LaTeXML/Pandoc HTML page).
  They compose the note against the raw selection (fine — the real document shows the formatted
  math). On **Save**, the annotation mini-interface becomes a **spinner** while the backend stores
  **and** normalizes it in one request; on success the card graduates to the stored annotation
  showing the **math-recovered** quote (`\(\mathcal{M}^0_{En}\)`, `RP²`, `D²`); on failure it kicks
  back to the same editor (note preserved) with a toast, and **Save** is the retry.
- Current defect: normalization is an async background worker; the store returns immediately with raw
  and the card renders raw. Wrong architecture (misread three times). The model is **synchronous**:
  store-and-normalize in one request, wait, show the normalized data, never raw.
- Observable completion: creating an annotation over rendered math returns a response whose stored
  card shows recovered LaTeX; a stored card never shows raw garble; if recovery genuinely errors the
  store **fails hard** and nothing is persisted.

## Confirmed design (locked — see Decision Log for the full Q&A)
1. **Everything on Save, synchronously.** Compose is unchanged (editor shows the raw selection while
   you write). **Save** triggers `POST /api/annotations`, which normalizes and commits the
   annotation + recovered quote in the **same transaction**; the response body is the normalized
   annotation. One round-trip; the "callback" is the response.
2. **A create requires a quote.** A create with no `TextQuoteSelector` is **rejected** — there are no
   quote-less annotations. (The old `urn:annotate:marker` session markers are a bad design being
   removed from the review-loop separately; nothing quote-less enters the DB.) This makes never-raw
   airtight.
3. **Source-first recovery, OCR fallback — always succeeds unless recovery genuinely errors.** When
   the page exposes math **source** in the DOM (arXiv LaTeXML, Pandoc/Quarto `<span class="math">`),
   extract the **exact authored TeX** (Node/KaTeX reconstruction) — perfect fidelity. Otherwise, and
   for all PDFs, **OCR the rendered region** (Mathpix). Every page/PDF is assumed to contain math; no
   heuristic gating.
4. **Fail fast, fail hard — data integrity is paramount.** Roll the store back (nothing persisted,
   toast, back to editor) on: OCR/Mathpix error or timeout; the region source can't be
   fetched/rendered; PDF region can't be located; an **empty** recovery from both source and OCR.
   **Non-empty** OCR output is **trusted** (confident-but-wrong OCR is undetectable; later batch LLM
   cleanup can improve stored rows). **No escape hatch** — never-raw is absolute; real failures must
   surface.
5. **Never raw = the sidebar card only.** A stored/displayed card never shows the raw text-layer
   quote. The in-page / on-PDF yellow highlight still anchors on the raw selection (untouched). The
   compose-time editor showing raw is fine.
6. **Rebuild clean.** Revert the async commits; rebuild reusing only the normalizers —
   `h/services/pdf_math.py` (Mathpix OCR + region location) and `h/scripts/html-normalize/`
   (Node/KaTeX source extraction) — after making them fail-hard and adding the OCR fallback for
   source-less HTML.

## Scope
**h (server) — in scope**
- `NormalizationService.normalize(annotation)` runs **synchronously** in the create path, inside the
  request transaction; a raise → `pyramid_tm` rolls back → nothing persists.
- **Require a quote:** the create schema/path rejects a create with no `TextQuoteSelector`.
- Recovery routing: HTML (http[s]) → Node/KaTeX **source extraction**; if it returns empty (no
  recoverable source) → **OCR fallback** on the rendered region; PDF (`PageSelector`) → OCR the
  rendered region (pymupdf render + Mathpix).
- `pdf_math.py` fails hard (raise, never return raw) on out-of-range page / locate-miss / empty OCR;
  the Node path raises on subprocess error; drop the `pdf_has_math`/`_html_has_math` heuristic gates.
- **30s configurable timeout** on the recovery call (h setting/env, default 30s); exceeding = failure.
- Keep `annotation_normalized` (`normalized_quote` + `method` = `html`/`ocr`) and log the outcome
  (method, latency) for observability. `method` is never `raw` now (raw ⇒ failure).
- Presenter surfaces `normalized_quote`; **no** status/error fields.

**client (fork) — in scope**
- Compose window unchanged (raw selection shown while writing the note).
- On Save: the annotation mini-interface (editor included) **becomes a spinner** — blocks the whole
  editor, **no cancel**, no optimistic raw render.
- Success → graduate to the normal stored-annotation card, showing `normalized_quote`.
- Failure → kick back to the **same** editor with the note **preserved** + a toast; **Save** retries.
- 30s request timeout.

**hypothesis-review — out of scope this pass**
- Revert my async-deletion commit back to its **original** state and leave it. The CLI/review-loop
  rework (drop the `urn:annotate:marker` mechanism → time-window like `slice`; read h's
  `normalized_quote`; drop `mathquote.py`/`pdfmath.py`) is a **separate later task** — the CLI is
  non-functional for math and unused until the backend is correct. Breaking it now is acceptable.

**Removed (wrong-model machinery — reverted, not patched)**
- Celery task `normalize_annotation` + the `AnnotationEvent` subscriber.
- `normalization_status` (pending/ready/failed), the `error` column, and its migration.
- `POST /api/annotations/{id}/normalize` retry endpoint + route.
- `hypothesis normalize-annotations` backfill CLI command.
- Client raw-fallback, status handling, per-annotation retry, notification gap.

**Data / ops**
- `annotation_normalized` table (pre-existing on h `main`) + anchoring (raw selectors never mutated).
- Existing annotations are **irrelevant and may be dropped** (dev data); no migration/backfill.
- The systemd dev service must load a direnv-allowed `.envrc` carrying `MATHPIX_API_KEY` so the
  **web/create** process has it.

## Invariants
- A persisted annotation always has a quote **and** a normalized row, created in the same
  transaction. No pending/failed persisted state; no quote-less rows.
- A stored annotation card **never** renders the raw text-layer quote.
- Any genuine recovery error (both source and OCR fail/empty) ⇒ **no** annotation row.
- Non-empty OCR output is trusted and stored as-is.

## Deferred sub-decision (pin at its task, not now)
- **OCR-fallback pixel source for source-less HTML:** where the rendered pixels come from — client
  screenshots the selection region, or the backend headless-renders it. Only bites source-less HTML
  (rare for arXiv LaTeXML). PDFs render server-side (pymupdf) as today.

## Task Plan (h)
- **T1. Clean base.** Revert the 4 async commits (or branch fresh from `main`). Keep `pdf_math.py` +
  the Node script; remove the task, subscriber, status/error column + migration, retry endpoint +
  route, backfill + tests.
- **T2. Fail-hard normalizers + OCR fallback.** `pdf_math.clean_pdf_quote` raises on
  out-of-range/locate-miss/empty; the Node path raises on subprocess error and returns empty when
  there's no recoverable source; add the OCR fallback for the empty-source HTML case; add the 30s
  timeout; drop the heuristic gates. Acceptance: unit tests — a source-bearing HTML quote returns the
  exact TeX; a PDF quote returns OCR LaTeX; each failure mode raises.
- **T3. Require-a-quote + synchronous service.** Reject quote-less creates; `normalize(annotation)`
  recovers and creates the row in the session, raising on any genuine failure. No status/error
  column. Acceptance: unit test — quote → row with recovered LaTeX; failure → raises, no row;
  quote-less create → rejected.
- **T4. Create-path hook.** `views/api/annotations.py:create` (or `AnnotationWriteService`) calls
  `normalize(annotation)` before the response, within the transaction; a raise → rollback.
  Acceptance: functional test — POST over math returns `normalized_quote`; a raising normalizer
  returns an error AND leaves the annotation count unchanged (rollback proven).
- **T5. Presenter.** `normalized_quote` surfaced; remove status/error.

## Task Plan (client)
- **T6.** Revert `5c1334d`; remove status/retry/notification handling.
- **T7.** Save flow: whole-editor spinner during the store request (no optimistic raw, no cancel);
  success → stored normalized card; failure → same editor, note preserved, toast; 30s timeout; never
  render raw in a stored card. Acceptance: component/integration tests — during a pending Save the
  editor shows the spinner and no raw quote; on resolve the stored card shows `normalized_quote`; on
  reject it returns to the editor with the note and a toast.

## System-Level Validation
- With h running (Mathpix key in the create process's env): annotate rendered math on an arXiv PDF →
  Save → spinner → stored card shows recovered LaTeX. Annotate a LaTeXML HTML page → exact TeX. Force
  a failure (Mathpix unreachable) → Save fails, nothing persisted, toast, back to editor; Save
  retries. Raw is never shown in a stored card.

## Risks / Recovery / Stop Rules
- **Held transaction.** Synchronous recovery keeps the DB transaction open for the OCR round-trip
  (≤30s). If lock/timeout issues appear, revisit (recover synchronously but outside the DB
  transaction, still before responding). Stop-and-ask if request/worker timeouts surface.
- Recovery: branches reverted from `main`; normalizers preserved + independently tested.

## Progress
- [ ] Unstarted — awaiting final go-ahead.

## Decision Log (finalized this session)
- **A — trigger/flow.** Everything on Save; compose shows the raw selection; Save → spinner while
  stored+normalized → success shows the normalized card; failure returns to the same editor + toast;
  Save = retry.
- **B — detection.** Always normalize; no heuristics; every page/PDF assumed to contain math; Mathpix
  replaceable by cheaper/local OCR later.
- **C — failure.** Fail fast and hard on all genuine failure modes; data integrity paramount; trust
  non-empty OCR; later batch LLM cleanup can improve rows; no escape hatches.
- **D — UI.** The mini-interface becomes the spinner (whole editor, no cancel); success graduates to
  the stored-annotation model; failure returns to the editor; Save is the retry; 30s configurable
  timeout.
- **E — coherence / scope.** CLI reads the normalized index with no own normalization — but it's a
  separate later task; the CLI can't be worked on or used until the backend is correct, so breaking
  it now is fine. Existing annotations irrelevant, may be dropped.
- **F — recovery model.** Source-first (exact authored TeX when the page exposes it), OCR the
  rendered region otherwise. Never fails on source availability — only on genuine errors.
- **G — markers / quote-less.** The `urn:annotate:marker` session markers are a bad design (they
  inject quote-less "garbage" into the annotation table); the review-loop should use a time-window
  (`slice`) instead. So the create **requires a quote**; quote-less creates are rejected.
- **H — never-raw scope.** Sidebar card only; in-page/on-PDF highlighting untouched; Mathpix key in
  the create process via direnv/systemd.

## Revision Notes
- Rev 1: initial plan after three architecture misreads; design locked via three confirmations.
- Rev 2: full grilling answered (A–F); fail-hard, always-normalize, on-Save spinner, CLI single
  source of truth.
- Rev 3: recovery model → source-first + OCR fallback (never fails on source availability); create
  **requires a quote** (markers are bad design, removed separately); hypothesis-review CLI rework
  deferred to a later task (backend-first); scope narrowed to h + client.
