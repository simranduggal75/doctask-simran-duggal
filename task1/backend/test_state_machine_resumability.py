"""
Proves behavior 2 ("survives being killed and resumed") with a real OS
process kill, not a simulated exception - subprocess runs
state_machine_worker.py, which hard-exits (os._exit, no cleanup) partway
through stage_b. A second subprocess invocation, pointed at the same
run_id, must resume from stage_b and complete, without re-running
stage_a or duplicating stage_b's completion.
"""

import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).parent / "test_helpers" / "state_machine_worker.py"


def _run_worker(
    db_path, run_id_file, results_file, die_in_stage=None, marker_file=None,
    stale_after_seconds=None,
):
    cmd = [sys.executable, str(WORKER), str(db_path), str(run_id_file), str(results_file)]
    if die_in_stage:
        cmd += ["--die-in-stage", die_in_stage, "--marker-file", str(marker_file)]
    if stale_after_seconds is not None:
        cmd += ["--stale-after-seconds", str(stale_after_seconds)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def test_killed_mid_stage_resumes_without_redoing_or_losing_work(tmp_path):
    db_path = tmp_path / "state.db"
    run_id_file = tmp_path / "run_id.txt"
    results_file = tmp_path / "results.txt"
    marker_file = tmp_path / "stage_b_started.marker"

    # First invocation: dies inside stage_b (hard os._exit, no cleanup).
    first = _run_worker(
        db_path, run_id_file, results_file,
        die_in_stage="stage_b", marker_file=marker_file,
    )
    assert first.returncode != 0, "worker should have hard-exited, not completed"
    assert "RUN_COMPLETED" not in first.stdout
    assert marker_file.exists(), "stage_b should have started before dying"

    # stage_a must have completed and committed before the crash.
    results_after_crash = results_file.read_text(encoding="utf-8").splitlines()
    assert results_after_crash == ["stage_a"], results_after_crash

    # Second invocation: same run_id, same marker file already present,
    # so stage_b runs for real this time instead of dying again.
    # stale_after_seconds=0 stands in for an operator/orchestrator that
    # knows the previous worker is confirmed dead (e.g. its OS process
    # no longer exists) and wants an immediate reclaim, rather than the
    # default conservative wait that protects against reclaiming a lock
    # that might still be held by a genuinely slow, live worker.
    second = _run_worker(db_path, run_id_file, results_file, stale_after_seconds=0)
    assert second.returncode == 0, second.stderr
    assert "RUN_COMPLETED" in second.stdout

    # Every stage ran exactly once - stage_a was NOT redone after resume,
    # stage_b and stage_c each ran exactly once.
    final_results = results_file.read_text(encoding="utf-8").splitlines()
    assert final_results == ["stage_a", "stage_b", "stage_c"], final_results

    # The run itself is marked completed, not stuck "running" forever.
    sys.path.insert(0, str(WORKER.parent.parent))
    from app.db import connect
    from app import state_machine as sm

    conn = connect(db_path)
    run_id = run_id_file.read_text(encoding="utf-8").strip()
    status = sm.get_run_status(conn, run_id)
    assert status["status"] == "completed"
    assert [s["status"] for s in status["stages"]] == ["completed", "completed", "completed"]


def test_resuming_an_already_completed_run_is_a_safe_no_op(tmp_path):
    db_path = tmp_path / "state.db"
    run_id_file = tmp_path / "run_id.txt"
    results_file = tmp_path / "results.txt"

    first = _run_worker(db_path, run_id_file, results_file)
    assert first.returncode == 0
    assert results_file.read_text(encoding="utf-8").splitlines() == ["stage_a", "stage_b", "stage_c"]

    # Running the exact same worker again against a completed run must
    # not duplicate any stage's work.
    second = _run_worker(db_path, run_id_file, results_file)
    assert second.returncode == 0
    assert "RUN_COMPLETED" in second.stdout
    assert results_file.read_text(encoding="utf-8").splitlines() == ["stage_a", "stage_b", "stage_c"]
