# PROGRESS.md — Task 1: Agentic Vendor Compliance Register

Log of assumptions, decisions, and notes made while working through task1/TASK.md.
One line per entry, appended as work happens.

## Assumptions

## Decisions
- Domain, architecture, and stack choices (construction vendor compliance; hand-rolled
  state machine over LangGraph; SQLite over Postgres server; plain HTML/JS over React;
  REST-first with MCP as a stretch) were proposed and explicitly approved by the user
  before any code was written - see task1/TASK.md for the full reasoning on each.

- Resumability (behavior 2) and concurrency safety (behavior 9) are implemented as the
  *same* mechanism, not two: claiming a stage is an atomic `BEGIN IMMEDIATE` compare-
  and-set. A "running" stage with a fresh lock is respected (genuine concurrent work,
  rejected with `StageAlreadyRunningError`); a "running" stage with a stale lock (older
  than a configurable threshold) is treated as orphaned from a crash and reclaimed. This
  is why a killed-and-restarted run and a concurrently-running second attempt are
  distinguished by the *same* staleness check rather than needing separate code paths
  that could disagree with each other.
- The stale-lock threshold is a parameter (`stale_after_seconds`), not a hardcoded
  constant, specifically so a resumed run can say "I know the previous worker is
  confirmed dead, reclaim immediately" (pass 0) versus the default conservative wait
  that protects a genuinely slow-but-alive worker from having its in-progress stage
  stolen out from under it. Proven by test: the naive "kill it then immediately resume
  with default settings" test correctly refused to reclaim (proving the safety property
  works), and only succeeded once the test explicitly asserted the old worker was dead.
- The subprocess-kill test for resumability uses a real `os._exit()` inside a real child
  process (via `test_helpers/state_machine_worker.py`), not a simulated exception in the
  same process - matches the task document's literal wording ("kill the process in the
  middle of a run") rather than a weaker in-process stand-in.
- Two real bugs the tests caught before this ever became "working, tested code":
  (1) `runs.pile_id` has a real foreign-key constraint (`PRAGMA foreign_keys=ON`) but
  `start_run` didn't auto-create the pile row, so any run against a not-yet-existing
  pile failed - fixed by having `start_run` idempotently insert the pile row inside its
  own transaction. (2) `_claim_stage` rolled back explicitly before raising in two
  branches, then the outer `except Exception: conn.execute("ROLLBACK")` tried to roll
  back an already-closed transaction and raised `OperationalError: cannot rollback - no
  transaction is active`, masking the real error. Fixed by removing the redundant
  explicit rollbacks and letting the outer handler own rollback uniformly.
- Vendor Compliance Register domain/corpus, architecture (hand-rolled state machine over
  LangGraph, SQLite over Postgres server, plain HTML/JS over React, REST-first with MCP
  as a stretch) - full reasoning for each in `task1/TASK.md`, approved by the user before
  any code was written.

## Open questions
