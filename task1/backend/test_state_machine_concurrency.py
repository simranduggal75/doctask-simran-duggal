"""
Proves behavior 9 ("two runs at the same time stay two runs") at two
levels:

- The same stage of the same run cannot be double-claimed by two live
  workers at once (real threads, real overlapping SQLite transactions -
  not mocked).
- Starting two runs of the same type against the same pile at the same
  time is rejected for the second one, not silently allowed to corrupt
  shared state - "the same pile hit twice" from the task's own wording.

Two runs against two DIFFERENT piles are independent by construction
(separate run_id/pile_id rows) and are exercised as part of the same
test file for completeness.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.db import connect
from app import state_machine as sm


def test_same_stage_cannot_be_double_claimed_concurrently(tmp_path):
    db_path = tmp_path / "state.db"
    conn_a = connect(db_path)
    conn_b = connect(db_path)

    run_id = sm.start_run(
        conn_a, pile_id="pile-1", run_type="test_run",
        stage_names=["slow_stage"],
    )

    started = threading.Event()
    release = threading.Event()
    errors = []
    claims = []

    def slow_worker(conn, worker_id):
        def stage(context):
            started.set()
            release.wait(timeout=5)
            return {"decision": "proceed"}

        try:
            sm.run_stages(conn, run_id, [("slow_stage", stage)], worker_id=worker_id)
            claims.append(worker_id)
        except sm.StageAlreadyRunningError:
            errors.append(worker_id)

    t1 = threading.Thread(target=slow_worker, args=(conn_a, "worker-1"))
    t1.start()
    started.wait(timeout=5)  # worker-1 has claimed the stage and is "mid-work"

    # worker-2 attempts the same stage while worker-1 still holds a fresh
    # (non-stale) lock - must be rejected, not allowed to run concurrently.
    t2 = threading.Thread(target=slow_worker, args=(conn_b, "worker-2"))
    t2.start()
    t2.join(timeout=5)

    release.set()
    t1.join(timeout=5)

    assert claims == ["worker-1"]
    assert errors == ["worker-2"]

    status = sm.get_run_status(conn_a, run_id)
    assert status["stages"][0]["status"] == "completed"


def test_second_run_of_same_type_on_same_pile_is_rejected_while_first_is_active(tmp_path):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)

    run_id_1 = sm.start_run(
        conn, pile_id="pile-1", run_type="understand", stage_names=["a"],
    )

    try:
        sm.start_run(conn, pile_id="pile-1", run_type="understand", stage_names=["a"])
        assert False, "expected ActiveRunConflictError"
    except sm.ActiveRunConflictError:
        pass

    # A different run_type against the same pile is unaffected.
    run_id_2 = sm.start_run(conn, pile_id="pile-1", run_type="examine", stage_names=["a"])
    assert run_id_2 != run_id_1

    # A different pile with the same run_type is also unaffected.
    run_id_3 = sm.start_run(conn, pile_id="pile-2", run_type="understand", stage_names=["a"])
    assert run_id_3 != run_id_1


def test_two_different_piles_run_independently_at_the_same_time(tmp_path):
    db_path = tmp_path / "state.db"
    conn_a = connect(db_path)
    conn_b = connect(db_path)

    results = {}

    def worker(conn, pile_id):
        run_id = sm.start_run(
            conn, pile_id=pile_id, run_type="understand", stage_names=["only_stage"],
        )

        def stage(context):
            time.sleep(0.05)
            return {"decision": "proceed"}

        sm.run_stages(conn, run_id, [("only_stage", stage)])
        results[pile_id] = sm.get_run_status(conn, run_id)["status"]

    t1 = threading.Thread(target=worker, args=(conn_a, "pile-a"))
    t2 = threading.Thread(target=worker, args=(conn_b, "pile-b"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results == {"pile-a": "completed", "pile-b": "completed"}


def test_orphaned_lock_from_a_dead_worker_is_reclaimable_not_permanently_stuck(tmp_path):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)

    run_id = sm.start_run(conn, pile_id="pile-1", run_type="test_run", stage_names=["a"])

    # Simulate a worker that claimed the stage and then vanished (crash)
    # without ever calling _finish_stage - stale_after_seconds=0 makes
    # any "running" lock immediately eligible for reclaim, standing in
    # for real elapsed time without an actual wait in the test.
    sm._claim_stage(conn, run_id, "a", "dead-worker", stale_after_seconds=120)

    def stage(context):
        return {"decision": "proceed"}

    outcomes = sm.run_stages(conn, run_id, [("a", stage)], stale_after_seconds=0)
    assert outcomes[0].status == "completed"
