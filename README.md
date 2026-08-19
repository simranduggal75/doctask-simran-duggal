# doctask-simran-duggal

Take-home submission for the SuperDocs Full-Stack AI Engineer round.

## What's in this repo

| Folder | What it is | Where it's actually submitted |
|---|---|---|
| [`task1/`](./task1) | **Task 1 — the shared agentic system.** An agentic vendor-compliance register: understands a pile of construction subcontractor documents, examines it against user-supplied rules, stays alive as new documents arrive, human-gated throughout. This is the primary deliverable for *this* repo — the task document specifically asks for Task 1 to live in its own private repository. | Here. See [`task1/README.md`](./task1/README.md) for everything: architecture, the five required behaviors and how each is proven, setup/run instructions, assumptions, known limitations. |
| [`task2/`](./task2) | **Task 2 — the assigned SuperDocs build.** A phone-first Daily Field Report pipeline for a construction superintendent, built on the SuperDocs API (upload → AI edit → human approval → export). | Task 2 builds are submitted separately as a pull request into the public [`superdocsapp/superdocs-builds`](https://github.com/superdocsapp/superdocs-builds) repo, under `use-cases/simranduggal75/daily-field-report/` — see [that PR](https://github.com/superdocsapp/superdocs-builds/pull/48). This folder is the development history; see [`task2/daily-field-report/README.md`](./task2/daily-field-report/README.md) for the build itself. |

`TASK.md` / `PROGRESS.md` at the repo root are the working log for Task 2's build
session (how the work was directed, assumptions made along the way). Task 1 has its
own equivalents at [`task1/TASK.md`](./task1/TASK.md) /
[`task1/PROGRESS.md`](./task1/PROGRESS.md), since the two tasks were built as
separate, self-contained efforts.

## Where to actually start reading

- Reviewing **Task 1**: go to [`task1/README.md`](./task1/README.md).
- Reviewing **Task 2**: go to the [merged/open PR into `superdocs-builds`](https://github.com/superdocsapp/superdocs-builds/pull/48), or [`task2/daily-field-report/README.md`](./task2/daily-field-report/README.md) for the same content viewed from this repo.
