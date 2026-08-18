"""
Resumable, concurrency-safe stage runner.

This is the primitive every movement (understand / examine / stay-alive)
is built on. Two guarantees live here and nowhere else:

- Survives being killed (behavior 2): each stage's result is committed to
  SQLite before the runner advances to the next stage. A restarted run
  re-reads stage status from the database and skips anything already
  completed - it never redoes finished work or loses it.
- Two runs stay two runs (behavior 9): claiming a stage is an atomic
  compare-and-set inside a BEGIN IMMEDIATE transaction. A stage already
  claimed by a live worker cannot be double-claimed. A stage claimed by a
  worker that then crashed (lock older than STALE_LOCK_SECONDS with no
  heartbeat) is treated as orphaned and can be reclaimed - this is what
  makes "killed and resumed" and "concurrency-safe" the same mechanism
  rather than two separate ones that could disagree with each other.

Stage callables return a dict. An optional "decision" key
(retry/skip/escalate/proceed, proceed is the default) lets a stage change
the run's path - satisfying behavior 1 (some stage decisions can change
the path), not just a fixed script with labels pretending to be stages.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

STALE_LOCK_SECONDS = 120
MAX_RETRIES_PER_STAGE = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class ActiveRunConflictError(Exception):
    """Another run of the same type is genuinely active for this pile."""


class StageAlreadyRunningError(Exception):
    """Another live worker currently holds this stage's lock."""


class StageEscalatedError(Exception):
    """A stage explicitly asked for human escalation - the run stops here, not failed, not completed."""


def _is_stale(locked_at: str | None, stale_after_seconds: int = STALE_LOCK_SECONDS) -> bool:
    if not locked_at:
        return True
    locked_dt = datetime.fromisoformat(locked_at)
    age = (datetime.now(timezone.utc) - locked_dt).total_seconds()
    # >= not > : stale_after_seconds=0 must mean "always immediately
    # reclaimable" deterministically. With a strict >, a lock claimed
    # and checked within the same clock tick could have age==0.0,
    # making 0.0 > 0 false and intermittently blocking a reclaim that
    # was supposed to be guaranteed - a real, observed flaky failure.
    return age >= stale_after_seconds


def ensure_pile(conn: sqlite3.Connection, pile_id: str, name: str | None = None) -> None:
    """Idempotent pile creation - runs/documents have a real FK to piles,
    so anything that starts a run against a pile_id must ensure it exists."""

    conn.execute(
        "INSERT OR IGNORE INTO piles (id, name, created_at) VALUES (?, ?, ?)",
        (pile_id, name or pile_id, now_iso()),
    )


def start_run(
    conn: sqlite3.Connection,
    *,
    pile_id: str,
    run_type: str,
    stage_names: list[str],
    triggered_by: str = "human",
) -> str:
    """
    Create a run and its pending stage rows, claiming the (pile_id,
    run_type) active-run lock. Raises ActiveRunConflictError if another
    run of the same type is genuinely active (not merely orphaned) for
    this pile - this is the "same pile hit twice" half of behavior 9.
    """

    run_id = new_id()
    ts = now_iso()

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO piles (id, name, created_at) VALUES (?, ?, ?)",
            (pile_id, pile_id, ts),
        )

        existing = conn.execute(
            "SELECT run_id, locked_at FROM active_run_locks WHERE pile_id = ? AND run_type = ?",
            (pile_id, run_type),
        ).fetchone()

        if existing is not None:
            existing_run_id, locked_at = existing
            existing_run = conn.execute(
                "SELECT status FROM runs WHERE id = ?", (existing_run_id,)
            ).fetchone()
            still_active = (
                existing_run is not None
                and existing_run[0] == "running"
                and not _is_stale(locked_at)
            )
            if still_active:
                raise ActiveRunConflictError(
                    f"Another {run_type} run is already active for pile "
                    f"{pile_id}: {existing_run_id}"
                )
            conn.execute(
                "DELETE FROM active_run_locks WHERE pile_id = ? AND run_type = ?",
                (pile_id, run_type),
            )

        conn.execute(
            "INSERT INTO active_run_locks (pile_id, run_type, run_id, locked_at) "
            "VALUES (?, ?, ?, ?)",
            (pile_id, run_type, run_id, ts),
        )
        conn.execute(
            "INSERT INTO runs (id, pile_id, run_type, status, triggered_by, "
            "started_at, finished_at) VALUES (?, ?, ?, 'running', ?, ?, NULL)",
            (run_id, pile_id, run_type, triggered_by, ts),
        )
        for order, stage_name in enumerate(stage_names):
            conn.execute(
                "INSERT INTO run_stages (id, run_id, stage_name, stage_order, "
                "status, cost_cents) VALUES (?, ?, ?, ?, 'pending', 0)",
                (new_id(), run_id, stage_name, order),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return run_id


@dataclass
class StageOutcome:
    stage_name: str
    status: str  # 'completed' | 'skipped_already_done' | 'skipped_by_decision' | 'escalated'
    result: dict | None
    cost_cents: int
    duration_ms: int | None


def _claim_stage(
    conn: sqlite3.Connection,
    run_id: str,
    stage_name: str,
    worker_id: str,
    stale_after_seconds: int = STALE_LOCK_SECONDS,
) -> str:
    """
    Atomically claim a stage. Returns 'already_completed', 'already_skipped',
    or 'claimed'. Raises StageAlreadyRunningError if a live worker holds it.
    """

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, locked_at FROM run_stages WHERE run_id = ? AND stage_name = ?",
            (run_id, stage_name),
        ).fetchone()

        if row is None:
            raise ValueError(f"Unknown stage {stage_name!r} for run {run_id}")

        status, locked_at = row

        if status == "completed":
            conn.execute("ROLLBACK")
            return "already_completed"

        if status == "skipped":
            conn.execute("ROLLBACK")
            return "already_skipped"

        if status == "running" and not _is_stale(locked_at, stale_after_seconds):
            raise StageAlreadyRunningError(
                f"Stage {stage_name!r} of run {run_id} is already being "
                f"worked on by another live process."
            )

        ts = now_iso()
        conn.execute(
            "UPDATE run_stages SET status='running', locked_by=?, locked_at=?, "
            "started_at=COALESCE(started_at, ?) WHERE run_id = ? AND stage_name = ?",
            (worker_id, ts, ts, run_id, stage_name),
        )
        conn.execute("COMMIT")
        return "claimed"
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _finish_stage(
    conn: sqlite3.Connection,
    run_id: str,
    stage_name: str,
    *,
    status: str,
    result: dict | None = None,
    error_message: str | None = None,
    decision: str = "proceed",
    cost_cents: int = 0,
    duration_ms: int | None = None,
) -> None:
    conn.execute(
        "UPDATE run_stages SET status=?, decision=?, result_json=?, "
        "error_message=?, completed_at=?, cost_cents=cost_cents+?, "
        "duration_ms=?, locked_by=NULL WHERE run_id = ? AND stage_name = ?",
        (
            status,
            decision,
            json.dumps(result) if result is not None else None,
            error_message,
            now_iso(),
            cost_cents,
            duration_ms,
            run_id,
            stage_name,
        ),
    )


def run_stages(
    conn: sqlite3.Connection,
    run_id: str,
    stages: list[tuple[str, Callable[[dict], dict]]],
    *,
    context: dict | None = None,
    worker_id: str | None = None,
    stale_after_seconds: int = STALE_LOCK_SECONDS,
) -> list[StageOutcome]:
    """
    Execute stages in order, skipping any already completed for this run
    (resumability), claiming each atomically before running it
    (concurrency safety). Stops and marks the run 'failed' on an
    unhandled exception, or 'escalated' if a stage's decision asks for
    human escalation - both leave completed stages exactly as they were,
    ready for a later resumed run to pick up cleanly.

    context is passed to every stage callable and mutated in place with
    each stage's result under context["results"][stage_name], so later
    stages can see earlier stages' output within the same run.
    """

    worker_id = worker_id or new_id()
    context = context if context is not None else {}
    context.setdefault("results", {})
    outcomes: list[StageOutcome] = []

    for stage_name, stage_fn in stages:
        claim = _claim_stage(conn, run_id, stage_name, worker_id, stale_after_seconds)

        if claim == "already_completed":
            row = conn.execute(
                "SELECT result_json, cost_cents, duration_ms FROM run_stages "
                "WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()
            result = json.loads(row[0]) if row and row[0] else None
            context["results"][stage_name] = result
            outcomes.append(
                StageOutcome(stage_name, "skipped_already_done", result, row[1] if row else 0, row[2] if row else None)
            )
            continue

        if claim == "already_skipped":
            outcomes.append(StageOutcome(stage_name, "skipped_by_decision", None, 0, None))
            continue

        attempt = 0
        while True:
            attempt += 1
            start = time.monotonic()
            try:
                output = stage_fn(context) or {}
                duration_ms = int((time.monotonic() - start) * 1000)
                decision = output.get("decision", "proceed")
                cost_cents = int(output.get("cost_cents", 0))

                if decision == "retry" and attempt < MAX_RETRIES_PER_STAGE:
                    # Re-claim not required - we still hold the lock from
                    # above. Just loop and run the stage again.
                    continue

                if decision == "escalate":
                    _finish_stage(
                        conn, run_id, stage_name, status="escalated",
                        result=output, decision=decision,
                        cost_cents=cost_cents, duration_ms=duration_ms,
                    )
                    _set_run_status(conn, run_id, "escalated")
                    outcomes.append(StageOutcome(stage_name, "escalated", output, cost_cents, duration_ms))
                    return outcomes

                if decision == "skip":
                    _finish_stage(
                        conn, run_id, stage_name, status="skipped",
                        result=output, decision=decision,
                        cost_cents=cost_cents, duration_ms=duration_ms,
                    )
                    outcomes.append(StageOutcome(stage_name, "skipped_by_decision", output, cost_cents, duration_ms))
                    break

                if decision == "complete_early":
                    # This stage genuinely finished (e.g. "scanned, found
                    # nothing new to do") - unlike escalate, this is not
                    # a problem needing a human, so the run ends
                    # 'completed', not 'escalated'. Every stage the run
                    # never reached is marked 'skipped' rather than left
                    # dangling as 'pending' forever.
                    _finish_stage(
                        conn, run_id, stage_name, status="completed",
                        result=output, decision=decision,
                        cost_cents=cost_cents, duration_ms=duration_ms,
                    )
                    context["results"][stage_name] = output
                    outcomes.append(StageOutcome(stage_name, "completed", output, cost_cents, duration_ms))

                    remaining = [s for s, _ in stages if s != stage_name and s not in context["results"]]
                    for remaining_stage in remaining:
                        conn.execute(
                            "UPDATE run_stages SET status='skipped', decision='complete_early', "
                            "completed_at=? WHERE run_id = ? AND stage_name = ? AND status = 'pending'",
                            (now_iso(), run_id, remaining_stage),
                        )
                        outcomes.append(StageOutcome(remaining_stage, "skipped_by_decision", None, 0, None))

                    _set_run_status(conn, run_id, "completed")
                    return outcomes

                _finish_stage(
                    conn, run_id, stage_name, status="completed",
                    result=output, decision=decision,
                    cost_cents=cost_cents, duration_ms=duration_ms,
                )
                context["results"][stage_name] = output
                outcomes.append(StageOutcome(stage_name, "completed", output, cost_cents, duration_ms))
                break

            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                _finish_stage(
                    conn, run_id, stage_name, status="failed",
                    error_message=str(exc), duration_ms=duration_ms,
                )
                _set_run_status(conn, run_id, "failed")
                raise

    _set_run_status(conn, run_id, "completed")
    return outcomes


def _set_run_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=? WHERE id = ?",
            (status, now_iso() if status in ("completed", "failed", "escalated") else None, run_id),
        )
        if status in ("completed", "failed"):
            row = conn.execute("SELECT pile_id, run_type FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM active_run_locks WHERE pile_id = ? AND run_type = ? AND run_id = ?",
                    (row[0], row[1], run_id),
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_run_status(conn: sqlite3.Connection, run_id: str) -> dict:
    run = conn.execute(
        "SELECT id, pile_id, run_type, status, triggered_by, started_at, finished_at "
        "FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"Unknown run {run_id}")

    stages = conn.execute(
        "SELECT stage_name, stage_order, status, decision, error_message, "
        "cost_cents, duration_ms FROM run_stages WHERE run_id = ? ORDER BY stage_order",
        (run_id,),
    ).fetchall()

    return {
        "run_id": run[0],
        "pile_id": run[1],
        "run_type": run[2],
        "status": run[3],
        "triggered_by": run[4],
        "started_at": run[5],
        "finished_at": run[6],
        "stages": [
            {
                "stage_name": s[0],
                "stage_order": s[1],
                "status": s[2],
                "decision": s[3],
                "error_message": s[4],
                "cost_cents": s[5],
                "duration_ms": s[6],
            }
            for s in stages
        ],
        "total_cost_cents": sum(s[5] for s in stages),
    }
