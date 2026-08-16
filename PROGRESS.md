# PROGRESS.md — Daily Field Report: Task 2 Completion

Log of assumptions, decisions, and notes made while working through TASK.md.
One line per entry, appended as work happens (not batched at the end).

## Assumptions
- Assumed the metadata status lifecycle is `pending` -> `processing` -> `awaiting_approval` ->
  `approved` -> `exported`, with `failed` reachable from `processing` or the approve step. If a
  SuperDocs job itself ends in `failed`/`rejected`/`cancelled` (not just a Python exception),
  metadata is also marked `failed` with that job status recorded as `failure_reason` — a report
  should never be left looking "in flight" when it actually died.
- Assumed that when both an explicit `project` (submission-time field) and an extracted `project`
  (from notes) are present and disagree, the explicit one wins for the metadata index (it was a
  deliberate choice, not a model guess), but the disagreement is preserved in
  `metadata.project_mismatch = {"explicit": ..., "extracted": ...}` rather than silently dropped.
- If only one of explicit/extracted project exists, that one is used with no mismatch flag.

## Decisions
- `POST /api/v1/reports` keeps doing synchronous SuperDocs processing (extraction -> captions ->
  HTML -> upload -> AI edit -> awaiting_approval) inside the request, same as before. Rationale:
  it's the working, tested path and gives responsive phone-UX/demo feedback. The nightly batch
  (Task 3) is idempotent catch-up for anything that never made it out of `pending`/`processing`
  (crashed request, dropped connection, etc.), not the primary path.
- Metadata writes go through a shared `report_store.py` (atomic write-via-rename) so
  `report_intake.py`, `report_pipeline.py`, the router, and the future batch/rollup services all
  read and update the same on-disk index instead of each hand-rolling JSON I/O.
- `report.json` (full `FieldReportData.model_dump(mode="json")`) is written next to
  `daily_field_report.html` right after the HTML is built, before the SuperDocs upload step —
  so even if SuperDocs fails, the extracted structured data is not lost.

- Assumed a small hand-picked synonym map (rain/storm/wind -> weather, hydraulic issue/breakdown ->
  equipment_failure, etc.) is enough for `cause_category` normalization, with anything unmatched
  falling back to `other` rather than raising or guessing a specific category. The extraction prompt
  now also instructs the model to emit one of the eight closed values directly, so the synonym map
  is a safety net for imperfect model output, not the primary mechanism.

- Nightly batch treats both `"pending"` (new lifecycle) and `"received"` (the status used by report
  dirs created before Task 1's metadata changes) as reprocessable. A pending report with neither
  notes.txt nor any photo files on disk is recorded as `skipped` rather than fed to `process_report`
  or silently dropped.
- Batch summaries are one file per calendar day (`storage/batches/{date}.json`) that gets
  **overwritten** by each run that day, not appended to. On-demand demo runs (via
  `POST /api/v1/batch/run`) will replace that day's file with the latest run's counts. Chose this
  over accumulating a list of runs per day to keep the summary format simple - "what happened today"
  answers the observability requirement without needing a history format. If per-run history turns
  out to matter later, this is the file to revisit.
- Batch processes reports sequentially, not concurrently - SuperDocs sessions are per-report and the
  API has already shown `409 session_busy` under concurrent jobs in the same session in prior
  testing (per TASK.md item on SuperDocs), so sequential is the safe default rather than an
  optimization left undone.

## Open questions
- A hard process crash mid-`processing` (not a caught exception) would leave metadata stuck in
  `processing` forever with no automatic recovery — acceptable for a file-based system at this
  scope, but worth calling out in the README as a known limitation rather than silently ignoring.
