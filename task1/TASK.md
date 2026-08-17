# TASK.md — Task 1: Agentic Vendor Compliance Register

## How to work with me
- Follow the disciplined GitHub workflow already established for Task 2: never commit
  directly to `main`, one GitHub issue per meaningful piece of work, branch from current
  `main`, implement, test, stage the diff for review. Commit/push/PR are mine to do once
  work is tested (established during Task 2 — issues + PRs are fine to open
  autonomously, merging is not). Never merge, never delete branches, without being asked.
- Work incrementally, one issue/branch at a time. Run tests after every meaningful change.
- Log assumptions in `task1/PROGRESS.md` as I go, same discipline as Task 2.
- Internal engineering deadline: Tuesday Aug 18 2026. Hard deadline: Thursday Aug 20 2026,
  11:59 PM IST. This is the single largest deliverable of the round — prioritize the five
  non-cuttable behaviors over breadth. If a scope cut is needed, cut inside the three
  movements or among behaviors 6-10, never the five-behavior floor, and document the cut
  and why in PROGRESS.md / the eventual README.
- Do not touch `task2/` — that work is finished, tested, and already submitted as a PR
  into `superdocs-builds`. Task 1 lives entirely under `task1/`.
- This private repo (`doctask-simran-duggal`) is the correct home for Task 1 per the
  task document: *"Task 1, the shared system: your own private repository; invite
  o-kadam."* No public PR for Task 1 — that's Task 2's requirement, not this one.

## What Task 1 actually requires (from the SuperDocs Engineer Task document)

Build an agentic system that owns a pile of related documents end to end, in a domain
the builder can genuinely stand behind. Three movements:

1. **Understand the pile.** Ingest mixed-format documents, identify what each one is,
   extract the facts that matter, notice where documents disagree, produce one grounded
   deliverable (register/brief/report). Every claim traces to the exact place in the
   sources it came from.
2. **Examine.** Given rules (a compliance checklist / playbook / style guide), check the
   sources and the deliverable against those rules in stages, produce findings that each
   point to their exact source. A clean corpus yields an honest "no findings" report -
   never fabricate a finding to have something to say.
3. **Stay alive.** New documents arrive into a watched location. Each arrival produces a
   *focused* update, not a full rewrite and not a re-run that happens to reproduce the
   same bytes - "an update should cost like an update." Untouched parts of the
   deliverable stay byte-identical, and the system can prove that. Contradictions from a
   new source are surfaced, not silently resolved. At any moment: what changed, when,
   because of which source.

A human gates all of it - conflicts, findings, and updates are approved or rejected by a
person before they commit.

### The five non-cuttable behaviors (the floor)

1. Visible staged pipeline; some stage decisions can change the path (retry/skip/
   escalate). Not one model call in a UI, not a fixed script with labels pretending to
   be stages.
2. Survives being killed mid-run and restarted - resumes from where it left off, no
   finished work lost or redone.
3. Human holds the gate - approves/rejects item by item in one review; rejecting one
   finding never discards the rest.
4. A machine can drive the whole flow end to end, no human clicking through a UI -
   approval is an operation the machine interface exposes explicitly.
5. Never bluffs - unsupported claims are stated as unsupported, not invented. A success
   message only ever means the output is genuinely in that state.

### Five more behaviors that separate strong from competent (cuttable, with a defended reason)

6. Fresh clone to working system in minutes, one documented command.
7. Real tests, runnable without a live model key - covering a run killed and resumed,
   two runs at once, and a document that tries to give the system orders.
8. Does not take orders from its own documents - a source document containing
   instructions aimed at the system is data to report on, never a command to follow.
9. Two runs at the same time stay two runs (same pile or different piles) - no state
   corruption.
10. Knows what it cost - a run can report spend and where time went, stage by stage.

### What NOT to build (rails from the task document)
- Not spreadsheet-editing output (reading a spreadsheet as a source is fine).
- Don't design around SuperDocs browsing the live web (not relevant here - Task 1 isn't
  built on the SuperDocs product, it's the separate shared agentic system).
- No live multiplayer editing with cursors.
- Not a document management system - we produce grounded deliverables, not an archive.
- No claimed certifications, no promised audited/guaranteed PII removal.

## Domain: Subcontractor Vendor Compliance Register

A general contractor (fictional: **"Meridian Builders GC"**) manages subcontractor
vendor contracts, the change-order amendments against them, and the invoices billed
against them. All three document types routinely disagree in practice - an invoice
billed at a pre-amendment rate, a change order never reflected in a later invoice, a
payment-terms clause an invoice ignores. Every document is synthetic, invented for this
build - no real client data, matching the task document's explicit requirement.

**Document types in the pile:** vendor contracts (PDF/text), change-order amendments
(PDF/text), invoices (PDF or CSV-like line items).

**The grounded deliverable:** a Vendor Compliance Register - one row per vendor contract,
current terms (as amended), invoices billed against it, and any detected disagreement
between what the contract+amendments say and what an invoice claims.

**The rules/playbook for the examine stage:** a compliance checklist such as - payment
terms in an invoice must match the contract as most-recently amended; every change order
must be signed by both parties before any invoice reflects its new terms; a vendor's
insurance certificate must be on file before their first invoice is processed. Rules are
user-supplied input, not hardcoded business logic baked into the pipeline - the same
checker must be able to run a different playbook without a code change.

## Architecture (lean, per-decision reasoning in PROGRESS.md)

- **Backend:** Python + FastAPI - kept from the recommended stack, no reason to deviate.
- **Orchestration:** hand-rolled step-based state machine, not LangGraph. Explicitly
  legitimate per the task document ("a hand-rolled agent loop" is listed as an equal
  choice). Full control over checkpointing semantics is the safer bet for getting
  behavior 2 (survives being killed) genuinely correct under a hard deadline, rather than
  learning a new framework's persistence guarantees from scratch and hoping they match
  what's actually required here.
- **Storage:** SQLite, not a Postgres server - relational state (documents, extracted
  facts, findings, run/stage status) plus a simple local vector table (stored embeddings,
  cosine similarity computed in-process) for retrieval. Real semantic retrieval, just no
  database server to stand up and operate under time pressure.
- **Review interface:** plain HTML/JS, same pattern already proven in Task 2's frontend -
  not a React build pipeline.
- **Machine interface:** REST API first (FastAPI provides this natively and it fully
  satisfies behavior 4 on its own). An MCP server wrapping the same operations is added
  only if time remains - it's explicitly "the strongest version" of behavior 4, not a
  requirement behavior 4 itself demands.

## Definition of done for this build

A watched-folder pile of synthetic vendor contracts/amendments/invoices, ingested and
understood into a grounded, source-linked Vendor Compliance Register; checked against a
user-supplied compliance playbook into findings (or an honest no-findings report);
staying alive as new documents arrive with focused, provably-untouched-elsewhere
updates; every conflict/finding/update gated by an explicit human approval step; the
whole flow drivable by a script with no UI involved; a killed run resumable with no lost
or duplicated work; two runs safely concurrent; real offline tests proving all of the
above, including a document that tries to instruct the system directly; a documented
one-command path from clone to running.
