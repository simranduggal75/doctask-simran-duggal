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

- Understanding is a pluggable interface (`app/llm_client.py`): a deterministic
  `HeuristicClient` (default, no API key, regex/pattern extraction grounded by
  construction - only ever extracts a fact when it can point to the literal text)
  and an `AnthropicClient` that activates automatically when `ANTHROPIC_API_KEY` is
  set, same output shape either way. Chose this because no key was available this
  session (user confirmed), and it keeps the system fully functional, demoable, and
  testable right now while leaving a real, honest upgrade path rather than a fake stub.
  **Not yet exercised against a live key** - flagging this explicitly rather than
  implying it's tested; the heuristic path is the one actually proven end-to-end.
- Disagreement detection (Movement 1's "notice where documents disagree") is kept
  distinct from Movement 2's rule-checking: Movement 1 flags internal inconsistency
  between the pile's own documents (an invoice not matching what its own contract +
  amendments say); Movement 2 (not yet built) will check the pile + deliverable
  against externally supplied compliance rules. Some real-world overlap is expected
  and fine - the rate mismatch here could also get caught by a "invoice must reflect
  current contract terms" rule later.
- Only *signed* amendments (both parties) are treated as changing a contract's
  effective terms - an amendment pending signature does not move the not-to-exceed
  amount or the applicable rate, even though its *content* describes an intended
  change. Verified live against the real corpus: Apex's unsigned amendment correctly
  left the register's NTE at the original $210,000, not the pending $224,500.
- The register is built as a new, versioned, *pending* `deliverable_versions` row on
  every `understand` run - re-running against an already-ingested pile does not
  silently overwrite the previous register or auto-approve anything; it produces
  version N+1, explicitly still pending. Ingestion itself is idempotent
  (content-hash keyed) so re-running doesn't re-import unchanged documents, but a
  fresh register version is still produced each run since facts may have changed.
- A real bug the corpus-level test caught: the injection-attempt detector originally
  split flagged text sentence-by-sentence, so one seeded injection note (spanning
  several sentences) was reported as 4 disconnected fragments instead of one
  coherent block a human reviewer could actually read. Fixed by grouping on
  paragraph boundaries instead of sentences.
- Verified live via plain `curl` against the running server (not just pytest): create
  pile -> POST /understand -> GET /register, end to end, no UI involved at any point -
  this is the concrete proof behind behavior 4 ("a machine can drive it").

## Open questions
- The heuristic extractor's reliability is a direct function of the corpus documents
  following a consistent "Label: Value" convention - realistic for one GC's own
  contract templates, not a claim that this generalizes to arbitrary third-party
  paperwork. The Anthropic path exists specifically to generalize better; it just
  hasn't been exercised without a key. Worth being explicit about this trade-off in
  the eventual write-up rather than letting the corpus-level test success imply more
  than it proves.
