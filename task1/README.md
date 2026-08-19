# Agentic Vendor Compliance Register

An agentic system that owns a pile of construction subcontractor documents end to
end: understands it, examines it against rules you supply, stays alive as new
documents arrive, and gates every commit behind a human — built for the SuperDocs
Full-Stack AI Engineer take-home task (Task 1, the shared agentic system).

## What it does, concretely

A general contractor manages subcontractor **vendor contracts**, the **change-order
amendments** against them, and the **invoices** billed against them. These three
document types routinely disagree with each other in the real world — an invoice
billed at a pre-amendment rate, a change order never reflected in a later invoice, a
payment-terms clause an invoice ignores. This system:

1. **Understands the pile** — ingests the documents, classifies them, extracts the
   facts that matter, notices where they disagree with each other, and produces one
   grounded Vendor Compliance Register. Every fact and every register row traces to
   the exact source document and excerpt it came from.
2. **Examines it** — checks the pile against a compliance playbook *you* provide at
   runtime (plain-text rules, not hardcoded business logic), producing findings that
   point to their exact source. A clean vendor with no violations produces an honest
   zero findings, not a fabricated one.
3. **Stays alive** — new documents dropped into the pile's directory produce a
   *focused* update: only the affected vendor's register row is recomputed, every
   untouched row is copied byte-identical from the previous version (provable by
   equality, not just claimed), and a contradiction against an *approved* baseline is
   surfaced as a distinct conflict rather than silently overwritten.

A human approves or rejects every register version and every finding — item by item,
in one batch review — before anything is considered final.

## Accepted inputs / domain

- **Format**: plain `.txt` documents with a consistent "Label: Value" convention (one
  GC's own contract/invoice templates, not arbitrary third-party paperwork — see
  "Known limitations" below). PDF/mixed-format ingestion was designed for but not
  built under the time available; see the cuts section.
- **Domain**: construction subcontractor compliance specifically — contracts,
  amendments, invoices, and insurance certificates for one general contractor's pile
  of vendors. All data is synthetic (a fictional GC, "Meridian Builders," and three
  fictional subcontractors) — see `backend/sample_data/meridian-builders/README.md`
  for the full, deliberately-seeded ground truth (which disagreements exist, which
  vendor is clean, where the prompt-injection fixture lives).
- A second run with a different pile of documents inside this same declared set
  works the same way — the corpus is a demo fixture, not something the code is
  hardcoded against (see Movement 2's test proving two different rule playbooks
  against the same corpus produce genuinely different results).

## Architecture

```
frontend/                    Plain HTML/JS review UI, no build step
  index.html, style.css, app.js
        |  fetch()
        v
backend/app/
  main.py                    FastAPI app; mounts routers + serves frontend/ (no-cache static files)
  db.py                      SQLite schema (WAL mode)
  state_machine.py           Resumable, concurrency-safe stage runner - the core primitive
  llm_client.py              Swappable understanding client (heuristic default / Anthropic when keyed)
  extraction.py              Pattern-based classification + fact extraction (the heuristic path)
  facts.py                   Shared read-side helpers: group facts by contract, detect
                              disagreements, build a register row - used by Movements 1 and 3
  rule_engine.py             User-supplied rule classification + checkers (Movement 2)
  movement_understand.py     Movement 1: ingest -> extract -> detect_disagreements -> build_register
  movement_examine.py        Movement 2: one dynamic stage per active rule -> compile_findings
  movement_incremental.py    Movement 3: detect_new -> ingest -> extract -> recompute -> rebuild
  routers/
    piles.py                 Create/list piles, run understand, get run status, get register
    examine.py                Rules CRUD, run examine, list findings
    watch.py                  Watch/scan, change log
    approvals.py               Approve/reject register versions, item-by-item findings review
sample_data/meridian-builders/  Synthetic corpus + documented ground truth
```

**Storage**: SQLite (WAL mode), not a Postgres server — relational state (documents,
facts, runs, findings, register versions, change log) with real foreign keys and
`PRAGMA foreign_keys=ON`. No queue, no cron daemon, no persistent watcher process.

## The five non-cuttable behaviors, and where they live

| Behavior | Where it's implemented | How it's proven |
|---|---|---|
| **1. Visible staged pipeline, decisions that change the path** | `state_machine.py`'s `decision` field (`proceed`/`retry`/`skip`/`escalate`/`complete_early`); Movement 2's rules become dynamic per-rule stages built from the actual rules loaded; Movement 3's `detect_new_documents` stage genuinely short-circuits the whole rest of the run when nothing is found | `test_examine_stages_are_genuinely_dynamic_per_rule`, verified live: a 2-rule examine run showed exactly 2 `check_rule_*` stages, not a fixed list; a no-op scan showed 1 real stage + 4 stages that never executed |
| **2. Survives being killed and resumed** | Every stage's result commits to SQLite before the runner advances; a restarted run skips anything already completed | `test_state_machine_resumability.py` — a real subprocess is launched and hard-exits (`os._exit`, no cleanup) mid-stage via `os._exit`; a second invocation resumes and completes without redoing or losing work |
| **3. Human holds the gate, item by item** | `routers/approvals.py`: register version approve/reject, and a *batch* findings-review endpoint where every decision applies independently | `test_approvals.py` — mixed approve/reject in one batch both persist; a bad finding_id never blocks the valid decisions around it; an already-decided item can't be silently re-decided |
| **4. A machine can drive it** | Every operation (ingest through approve) is a REST endpoint; approval is an explicit call | Verified live via plain `curl`, no UI involved: create pile → understand → add rules → examine → review findings → approve register, entirely through HTTP, across all three movements in one flow |
| **5. Never bluffs** | Every fact carries a literal `source_excerpt`; unrecognized rules are reported `unsupported`, not silently dropped or faked; a clean vendor produces zero findings, not a manufactured one | `test_every_fact_has_a_real_source_excerpt`; `test_unrecognized_rule_is_reported_honestly_not_silently_dropped`; `test_summit_is_fully_clean_honest_no_findings` |

## Strong-vs-competent behaviors (6-10) — what's built, what's cut

- **6. Stranger can run it in minutes** — one command, see below.
- **7. Real tests, no live key** — 44 tests, verified by removing `.env` entirely and
  re-running (`conftest.py` sets a placeholder key only for import-time satisfaction,
  never used to make a real call). Covers all three of the task's named claims as
  **dedicated, standalone tests**: `test_state_machine_resumability.py` (real
  subprocess kill), `test_state_machine_concurrency.py` (real overlapping threads),
  `test_injection_resistance.py` (a document trying to give orders).
- **8. Does not take orders from its documents** — `extraction.py`'s
  `detect_injection_attempts` flags text reading as an instruction directed at the
  system and reports it as data (`embedded_instruction_to_system` facts), never acts
  on it. `test_injection_resistance.py` proves this against **four simultaneous
  techniques** in one adversarial document: a fake system note, an instruction
  embedded inside a line-item description, a false total claim, and an instruction to
  suppress a specific compliance finding — the suppressed finding is raised anyway.
- **9. Two runs stay two runs** — `test_state_machine_concurrency.py`: a live-claimed
  stage can't be double-claimed; a second run of the same type against the same pile
  is rejected while the first is active; two different piles run independently; an
  orphaned lock from a dead worker is reclaimable, not permanently stuck.
- **10. Knows what it cost** — every stage reports `cost_cents` and `duration_ms`;
  `total_cost_cents` is the literal sum. `HeuristicClient` honestly reports `$0` (no
  API call was made); `AnthropicClient` computes real cost from token usage against
  published pricing, verified with a fake metered client since the heuristic path
  can't exercise non-zero accumulation.

**Cut, deliberately**: an MCP server wrapping the same operations. The task
document frames MCP as *"the strongest version"* of behavior 4, not a requirement
behavior 4 itself demands — REST alone satisfies it, and does so here with live,
cross-movement proof. Under the time available, correctness on the five non-cuttable
behaviors took priority over adding a second machine-interface surface that would
have wrapped, not replaced, the REST layer already proven to work.

## Setup / run

Requires Python 3.11+. No API key required to run or test — the system is fully
functional on the deterministic heuristic path.

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8001
```

Open `http://127.0.0.1:8001/` for the review UI, or drive it entirely via REST (see
below). To use real LLM-based extraction instead of the heuristic path, set
`ANTHROPIC_API_KEY` in the environment before starting the server — no code changes
needed, `llm_client.get_client()` picks it up automatically.

### Tests

```bash
cd backend
pytest
```

44 tests, no live model key needed (verified by removing `.env` entirely and
re-running).

### Driving it end to end via REST

```bash
# 1. Create a pile pointing at the sample corpus
curl -X POST http://127.0.0.1:8001/api/v1/piles \
  -H "Content-Type: application/json" \
  -d '{"name": "Meridian Builders GC", "source_dir": "/absolute/path/to/backend/sample_data/meridian-builders"}'

# 2. Understand it (produces the grounded register)
curl -X POST http://127.0.0.1:8001/api/v1/piles/{pile_id}/understand

# 3. Add a compliance rule and examine
curl -X POST http://127.0.0.1:8001/api/v1/piles/{pile_id}/rules \
  -H "Content-Type: application/json" \
  -d '{"rule_text": "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed."}'
curl -X POST http://127.0.0.1:8001/api/v1/piles/{pile_id}/examine

# 4. Review findings item by item, then approve the register
curl -X POST http://127.0.0.1:8001/api/v1/piles/{pile_id}/findings/review \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"finding_id": "...", "decision": "approve"}]}'
curl -X POST http://127.0.0.1:8001/api/v1/piles/{pile_id}/register/1/approve
```

Full endpoint list, request/response shapes: `http://127.0.0.1:8001/docs` (FastAPI's
generated OpenAPI UI).

## Assumptions and decisions

Pulled from the full working log in [`PROGRESS.md`](./PROGRESS.md); the ones that
most shaped the build:

- **Hand-rolled state machine over LangGraph.** Full control over checkpointing
  semantics was the safer bet for getting "survives being killed" genuinely correct
  under a hard deadline than learning a new framework's persistence guarantees from
  scratch. Explicitly legitimate per the task document ("a hand-rolled agent loop" is
  listed as an equal choice to LangGraph/AutoGen/CrewAI).
- **SQLite over a Postgres server.** Real relational state and real foreign keys,
  just no database server to stand up and operate under time pressure.
- **Resumability and concurrency safety are the same mechanism**, not two: claiming a
  stage is an atomic compare-and-set; a fresh lock is respected, a stale one is
  reclaimed. This is why a killed-and-resumed run and a genuinely concurrent second
  attempt can't disagree with each other about what's safe.
- **Movement 1's disagreement detection and Movement 2's rule-checking are
  deliberately independent implementations**, not a shared refactor, despite real
  logic overlap — a conscious trade-off to avoid touching already-merged, tested
  Movement 1 code under deadline pressure. The distinction the task draws is real:
  Movement 1 is a fixed, always-on baseline; Movement 2 is user-configurable,
  runtime-supplied policy.
- **Only signed amendments change effective contract terms.** An amendment pending
  countersignature describes an intended change but doesn't move the register's
  numbers — verified live: an unsigned amendment correctly left the register's
  not-to-exceed amount at the original value, not the pending one.
- **Findings are deduplicated on re-examine** (same rule, same title, same
  description, not rejected) but a rejected finding can recur as a fresh row — a
  rejection is a decision about one review, not a permanent suppression rule. Found
  as a real bug during live UI testing (see below), not designed in from the start.
- **Watching is polling** (`POST /watch/scan`), not a persistent filesystem-watcher
  process — consistent with "no new infrastructure" and the precedent Task 2 set with
  its nightly batch. The requirement is about behavior (focused updates on arrival),
  not implementation mechanism.

## What testing actually found (and fixed)

Real bugs, not hypothetical ones — caught by writing tests and by driving the system
live (curl and a real browser), not assumed away:

- A foreign-key constraint gap where `start_run` didn't auto-create its pile row.
- A double-rollback in the state machine that masked the real error under
  `OperationalError`.
- A staleness-comparison edge case (`age > 0` vs `age >= 0`) that made
  "reclaim immediately" non-deterministic under fast clock ticks — found by running
  the test suite repeatedly, not on the first pass.
- A line-item parser that silently dropped legitimate text when a description wrapped
  across two lines in a way the original sample corpus never happened to exercise.
- An injection-pattern list narrowly tuned to one phrasing, broadened after writing a
  genuinely adversarial (not just similar) test document.
- A cost-tracking bug (`int()` truncation) that would have silently reported $0
  forever even with a real API key, since a single document's real cost is very
  likely sub-cent.
- An idempotency gap where re-running `examine` duplicated findings — found only by
  testing in a real browser, not by pytest.
- A stale-cache bug in the static file mount that would have silently served old
  review-UI logic to real users after any future deploy.

Every one of these is documented with its root cause and fix in `PROGRESS.md`, not
just fixed silently.

## Known limitations

- **The heuristic extractor's reliability is a direct function of the corpus
  documents following a consistent "Label: Value" convention** — realistic for one
  GC's own contract templates, not a claim that this generalizes to arbitrary
  third-party paperwork. `AnthropicClient` exists specifically to generalize better;
  it has not been exercised against a live key this session (no key was available —
  confirmed with the user rather than assumed), so its correctness is not proven the
  way the heuristic path's is.
- **A hard process crash mid-`processing`** (not a caught exception — the OS process
  itself dying) leaves that stage's lock stale but the run's own status un-updated
  until something re-checks it. Acceptable at this scope for a file-based system, but
  a real limitation, not a silently-ignored one.
- **PDF/mixed-format ingestion was designed for but not built.** The pile only reads
  `.txt` files today; the extraction interface is format-agnostic in shape, but no
  PDF-to-text adapter exists.
- **MCP server**: deliberately cut, see above.
