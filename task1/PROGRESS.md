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

- Movement 2's rule checkers are a deliberately independent implementation from
  Movement 1's disagreement detection, not a shared refactor - some logic overlap
  (both ultimately check insurance/signatures/rates against similar facts) is a
  conscious trade-off to avoid touching already-merged, tested Movement 1 code under
  deadline pressure. The distinction the task draws is real: Movement 1's checks are a
  fixed, always-on baseline (internal consistency of the pile), Movement 2's are
  user-supplied, runtime-configurable policy (an external playbook) - genuinely
  different concerns that happen to share underlying data.
- Each active rule becomes its own dynamically generated stage (`check_rule_<id>`),
  built from the rules actually loaded for that pile at run-start - not a fixed
  script. Verified live: a run against 2 real rules showed exactly 2 `check_rule_*`
  stages plus `compile_findings` in the run status, not a static 3-stage list.
- Rule classification is keyword-pattern matching (all-of-a-group required per
  concern), not a hardcoded 1:1 rule-to-checker mapping - one rule's text can match
  zero, one, or multiple checkers. A rule matching nothing is reported as
  `unsupported`, distinct from and never confused with "checked, found nothing" -
  verified by test (a "safety vests" rule correctly produces 0 findings AND is listed
  as unsupported, not silently absorbed into a clean result).
- Proved genuine rule-drivenness (not the same hardcoded checks relabeled) three ways:
  a playbook with only the insurance rule finds only the insurance issue; a different
  playbook (change-order-signed + not-to-exceed, no insurance rule) omits the
  insurance finding entirely and correctly finds zero NTE breaches (an honest zero for
  a check Movement 1 never even performed); the full 3-rule playbook reproduces
  Movement 1's exact finding count (3) through an entirely independent code path.
- A real, non-deterministic bug found running the full test suite repeatedly (passed
  in isolation, failed intermittently in the full run): `_is_stale`'s `age >
  stale_after_seconds` comparison meant `stale_after_seconds=0` ("reclaim
  immediately") wasn't actually guaranteed - if claim-then-check happened within the
  same clock tick, `age` could be exactly `0.0`, and `0.0 > 0` is `False`. Fixed by
  changing to `>=`. Confirmed fixed by running the full suite 5 times consecutively
  with no failures (was previously observed to fail roughly 1 in 6-7 runs).

- Added a new state-machine decision, `complete_early` (additive to #17's core, all
  existing tests still pass unchanged): a stage can legitimately finish a run
  successfully with nothing more to do (e.g. a watch scan finding no new documents),
  distinct from `escalate` (needs a human) and `skip` (skips only the current stage).
  Every stage the run never reached is marked `skipped` in the DB rather than left
  dangling `pending` forever. Verified live: a no-op scan's run status showed exactly
  one `completed` stage and four genuinely-never-executed `skipped` stages.
- Extracted two more pure functions this movement needed to share with Movement 1
  (`detect_disagreements_for_contract`, `build_vendor_row`) into `app/facts.py`,
  replacing Movement 1's inline copies rather than duplicating a third time. Verified
  behavior-preserving by re-running the full suite (including the exact-ground-truth
  corpus tests) after each extraction before moving on - a refactor that silently
  changed Movement 1's output would have shown up immediately as a test failure.
- Watching is polling-based (`POST .../watch/scan`), not a persistent
  filesystem-watcher process - consistent with the "no new infrastructure" stack
  decision and the precedent Task 2 set with its nightly batch. The task's requirement
  is about behavior (focused updates on arrival), not implementation mechanism.
- "Focused update" is provable, not just asserted: every vendor row an incremental
  scan's new document(s) did not reference is copied by dict identity from the
  previous version's content, and the test suite asserts `==` equality on those rows
  across versions (not "looks similar" - literal Python equality on the parsed JSON).
  Verified live too: the register's own `change_summary` self-reports "N row(s)
  recomputed, M row(s) unchanged" and a real scan showed 1 recomputed / 2 unchanged
  for a single new invoice.
- Conflict detection compares a newly recomputed vendor row against the pile's latest
  *approved* version specifically (not merely its latest version) - an update that
  disagrees with something still pending isn't a "conflict" in the same sense, since
  nothing has been committed to yet. Issue #13 (not yet built) will add the actual
  approve endpoint; tested here by setting a version's status to 'approved' directly,
  which is exactly what that endpoint will do.
- Findings review is one batch endpoint (`POST .../findings/review`) accepting a list
  of `{finding_id, decision, reason?}`, applying each independently rather than
  all-or-nothing - matches the task's own wording ("approves what is right and rejects
  what is wrong in the same review, item by item... rejecting one finding does not
  discard the rest") more directly than N separate per-finding calls would. Verified:
  one bad finding_id in a batch never blocks the valid decisions around it, and an
  approve+reject pair in the same call both persist independently.
- Both the finding-review and register-version-approval paths treat "already decided"
  as an explicit, reported condition (409 for versions, `applied: false` +
  `existing_status` for findings) rather than silently no-op'ing or silently
  overwriting a prior human decision - a second call is never allowed to quietly flip
  what a person already decided.
- Full REST-only flow verified live (curl, no UI): create pile -> understand -> add 2
  rules -> examine -> review findings (one approved, one rejected, single batch call)
  -> approve the register version -> confirmed final state (both finding statuses and
  the register's approved status) entirely through HTTP calls. This is the concrete
  evidence behind floor behavior 4, exercised across all three movements together for
  the first time in one flow, not just within one movement's own tests.

## Open questions
- The heuristic extractor's reliability is a direct function of the corpus documents
  following a consistent "Label: Value" convention - realistic for one GC's own
  contract templates, not a claim that this generalizes to arbitrary third-party
  paperwork. The Anthropic path exists specifically to generalize better; it just
  hasn't been exercised without a key. Worth being explicit about this trade-off in
  the eventual write-up rather than letting the corpus-level test success imply more
  than it proves.
