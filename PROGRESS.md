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

## Open questions
- A hard process crash mid-`processing` (not a caught exception) would leave metadata stuck in
  `processing` forever with no automatic recovery — acceptable for a file-based system at this
  scope, but worth calling out in the README as a known limitation rather than silently ignoring.
