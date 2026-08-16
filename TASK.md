# TASK.md — Daily Field Report: Task 2 Completion

## How to work with me
- Inspect before editing. Don't rewrite working code, don't delete working functionality.
- Work incrementally: one phase at a time, run relevant tests after each change, report failures honestly.
- Do NOT commit, push, merge, or delete branches unless explicitly asked.
- Do NOT touch anything under a separate Task 1 system if one exists in this repo — out of scope.
- Branch to work on: `feat/daily-report-completion` (create from `main` if it doesn't exist).
- Log assumptions as you go in a `PROGRESS.md` in this same folder — one line per assumption, e.g.
  "Assumed nightly batch means idempotent reprocessing of pending intake, not a hard 24h delay."
- Internal engineering deadline: Tuesday Aug 18. Hard deadline: Thursday Aug 20, 11:59 PM IST.
  Prioritize required behavior over polish. Don't over-engineer, don't add infra (Postgres, Redis,
  queues, cron daemons) that a simple file-based idempotent job/service can cover.

## Where the code lives
`task2/daily-field-report/backend/` — FastAPI app.
- `app/config.py`, `app/main.py`
- `app/models/field_report.py` — `FieldReportData` and sub-models
- `app/routers/reports.py` — `POST /api/v1/reports`, `POST /api/v1/reports/{id}/approve`
- `app/services/report_intake.py` — saves notes + photos to `storage/reports/{report_id}/`
- `app/services/report_extractor.py` — SuperDocs chat() → JSON → `FieldReportData`
- `app/services/report_pipeline.py` — orchestrates extraction → photo captions → HTML → SuperDocs
- `app/services/report_builder.py` — `build_daily_report_html(...)`
- `app/services/superdocs_service.py` — the four SuperDocs calls (upload/chat/approve/export)
- `test_*.py` — manual asyncio scripts that hit the live SuperDocs API (no pytest, no mocks)

## Already working — treat as correct unless you find a concrete bug
- Field-report extraction (manpower, equipment, work_by_area, weather, delays, safety) with a
  "never invent facts" prompt. Tested successfully against realistic notes.
- Photo relevance/caption logic — correctly rejects non-construction images.
- `report_builder.py` — HTML-escapes everything, no `photo_captions` reset bug present.
- All four SuperDocs contract calls implemented and tested: upload, async chat (edit), approve,
  export. `export_document()` correctly reads `response.content` as binary, not `.json()` —
  do not regress this, it was a real bug that was already fixed once.
- Notes-only / photos-only / notes+photos are all handled without crashing.

## Confirmed gaps (this is the actual remaining work)

### 1. Persist structured report data + a project/date index (do this first — everything else depends on it)
- `process_report()` in `report_pipeline.py` builds a `FieldReportData` object and turns it straight
  into HTML — it's never written to disk as JSON. Add: write `report.json`
  (the `FieldReportData.model_dump(mode="json")`) next to `daily_field_report.html` in the report dir.
- `report_intake.py`'s `metadata.json` only stores `report_id`, `has_notes`, `photo_count`, `photos`,
  `status`. It never stores `project` or `report_date` — those only exist inside the LLM-extracted
  data, buried in HTML. Add `project`, `report_date`, and a real `status` lifecycle
  (`pending` → `processing` → `awaiting_approval` → `approved`/`exported` → `failed`) to metadata,
  updated once extraction/processing completes. This index is what nightly batching and the weekly
  rollup will query — don't make them re-parse HTML.
- Consider: let the phone intake form optionally pass an explicit `project` field (dropdown or free
  text) at submission time, since photo-only reports have no notes to extract a project name from,
  and batching "across projects" needs a reliable project identifier. If both an explicit project and
  an extracted project exist and disagree, don't silently pick one — surface the mismatch in the
  report status rather than guessing.

### 2. Constrain `cause_category` to a fixed vocabulary
- Currently `DelayEntry.cause_category` is a free `str | None`, and the extraction prompt lets the
  model write anything ("rain", "weather delay", "Weather"...). The assigned-build spec explicitly
  says strong = "delay causes are categorized consistently enough to roll up meaningfully" — right
  now three reports about rain would produce three different strings and nothing would roll up.
- Fix: define a small closed set, e.g. `weather`, `equipment_failure`, `material_delay`,
  `labor_shortage`, `permitting_inspection`, `design_coordination`, `site_conditions`, `other`.
  Update the Pydantic model (Literal or enum) and the extraction prompt to map into this set, with
  `other` + the original wording preserved in `description` as fallback. This is a prompt/config
  change, not a rewrite of the extractor.

### 3. Nightly batch service
- Nothing currently scans for pending reports or processes them as a batch — every report is
  processed synchronously and immediately inside the `POST /api/v1/reports` request handler.
- Build `app/services/nightly_batch.py`: scans `storage/reports/*/metadata.json` for
  `status == "pending"` (or whatever hasn't reached a terminal state), processes each through the
  existing `process_report()` pipeline, and:
  - Isolates failures per report — one project's bad input must not stop the batch for others.
  - Is idempotent — running it twice must not reprocess or duplicate an already-processed report.
  - Writes an observable batch summary (e.g. `storage/batches/{date}.json`: counts of
    processed/failed/skipped, and which report_ids failed and why).
  - Never emits a half-empty report silently (per the "what strong looks like" line in the spec) —
    a report that fails partway should be marked `failed` with a reason, not left looking complete.
- Expose it two ways: a runnable script (`python -m app.services.nightly_batch`) for a real cron/
  scheduled task, and `POST /api/v1/batch/run` so it can be demoed on-demand without waiting for a
  real night to pass.
- Decide and document (in PROGRESS.md) whether `POST /api/v1/reports` keeps doing synchronous
  SuperDocs processing (better phone UX / demo responsiveness) with the batch job as idempotent
  catch-up for anything left pending, or whether intake becomes pending-only and batch does all
  SuperDocs work. Either is defensible — just state the choice and why.

### 4. Weekly rollup service
- Nothing aggregates across reports yet. Build `app/services/weekly_rollup.py`: given a project and
  a week (or date range), read the persisted `report.json` files from step 1 that fall in range,
  and produce:
  - A progress summary grounded only in what the daily reports actually say.
  - Recurring delay causes — tally `cause_category` across the week's reports, surface ones that
    recur (e.g. appear in 2+ reports), using the fixed vocabulary from step 2.
  - Every claim in the rollup must link back to the specific `report_id`(s)/date(s) that support it.
  - If there's no data to support a claim, say so — never fabricate a trend from an empty or
    single-report week.
- Output as HTML (reuse the `report_builder.py` escaping approach) with a link/reference section per
  claim. Pushing it through the SuperDocs edit/approve/export loop too is a nice bonus but is
  optional depth — get the aggregation and grounding right first.

### 5. Minimal phone-first frontend
- No frontend exists anywhere in the repo currently — confirmed by full file listing.
- Keep it small: one page, mobile-first layout, plain HTML/JS (or React if you'd rather match the
  SuperDocs house stack — either is fine, this is your call). Needs to support the full loop:
  1. Enter field notes (textarea) + optionally select/type project name
  2. Upload one or more photos from the phone
  3. Submit → see structured report (manpower/equipment/work/weather/delays/safety)
  4. See photo captions + relevance
  5. See SuperDocs proposed changes (remember: proposed-change content arrives as a JSON-encoded
     string and needs a second `json.loads` — the final result is already an object; missing this
     is the most common integration bug people hit)
  6. Approve changes (button → calls the approve endpoint)
  7. Get/download the exported PDF
  - Clear loading and error states throughout — SuperDocs operations can take 30s to several minutes
    with no visible progress; that's normal, not a crash. Show a "still processing" state, not a
    frozen spinner with no explanation.

### 6. Tests for the new pure-logic pieces
- The existing `test_*.py` scripts are honest manual/live-API scripts — leave them as-is.
- Add real, offline-runnable tests (pytest, no live key needed) for anything that's pure logic and
  doesn't require calling the live SuperDocs API: cause-category normalization, batch idempotency
  (run twice, assert no duplicate processing), weekly rollup aggregation and grounding (feed it
  fabricated `report.json` fixtures, assert recurring-cause detection and that it never invents a
  claim beyond the fixtures given).

### 7. Small fixes along the way
- `requirements.txt` is saved in broken UTF-16 encoding — re-save as UTF-8 plain text or
  `pip install -r` will likely fail on a fresh clone.
- Double-check `approve_changes()` in `superdocs_service.py` against the "second parse" note above —
  right now it only reads `change["change_id"]` off each pending change, which may be fine, but
  verify against a real proposed-change payload before assuming it's unaffected.

### 8. README + screenshot
- Do this last, once the above is real and tested. Cover: accepted input formats/domains,
  assumptions made (pull from PROGRESS.md), setup/run instructions, architecture/flow diagram or
  description, and a screenshot of the frontend. This stays local for now — do NOT open a PR into
  `superdocs-builds` until told to.

## Definition of done for this session
Rough notes + photos → structured report → photo captions → nightly batch (idempotent, resilient to
partial input, observable) → weekly rollup (grounded, linked back to daily reports) → SuperDocs
upload → AI edit → human approval → exported PDF, all reachable from a working phone-first page,
with tests passing and README + screenshot in place.
