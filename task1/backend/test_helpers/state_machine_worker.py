"""
Standalone worker for exercising the state machine as a real OS process -
so tests can prove "killed mid-run, restarted, resumes cleanly" against
an actual SIGKILL/os._exit, not just a simulated exception inside the
same process.

Runs three dummy stages (a, b, c), each appending its name to a results
file so a test can see exactly what ran. If --die-in-stage names a stage
and --marker-file doesn't exist yet, that stage writes the marker file
then hard-exits (os._exit, no cleanup, no exception handler runs) before
completing - simulating a real crash mid-stage. On a later invocation
with the same marker file already present, that stage runs normally, so
the same script doubles as both the "kill" run and the "resume" run.

Usage:
    python state_machine_worker.py <db_path> <run_id_file> <results_file> \
        [--die-in-stage stage_b] [--marker-file path] [--pile-id id] \
        [--stale-after-seconds N]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app import state_machine as sm


def make_stage(name: str, results_file: Path, die_in_stage: str | None, marker_file: Path | None):
    def stage(context: dict) -> dict:
        if name == die_in_stage and marker_file is not None and not marker_file.exists():
            marker_file.write_text("started", encoding="utf-8")
            os._exit(1)  # hard kill - no exception handler, no cleanup

        with results_file.open("a", encoding="utf-8") as f:
            f.write(name + "\n")

        return {"decision": "proceed", "cost_cents": 1}

    return stage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("run_id_file")
    parser.add_argument("results_file")
    parser.add_argument("--die-in-stage", default=None)
    parser.add_argument("--marker-file", default=None)
    parser.add_argument("--pile-id", default="test-pile")
    parser.add_argument("--stale-after-seconds", type=float, default=sm.STALE_LOCK_SECONDS)
    args = parser.parse_args()

    conn = connect(args.db_path)
    results_file = Path(args.results_file)
    marker_file = Path(args.marker_file) if args.marker_file else None

    run_id_file = Path(args.run_id_file)
    if run_id_file.exists():
        run_id = run_id_file.read_text(encoding="utf-8").strip()
    else:
        run_id = sm.start_run(
            conn, pile_id=args.pile_id, run_type="test_run",
            stage_names=["stage_a", "stage_b", "stage_c"],
        )
        run_id_file.write_text(run_id, encoding="utf-8")

    stages = [
        (name, make_stage(name, results_file, args.die_in_stage, marker_file))
        for name in ["stage_a", "stage_b", "stage_c"]
    ]

    sm.run_stages(conn, run_id, stages, stale_after_seconds=args.stale_after_seconds)
    print("RUN_COMPLETED")


if __name__ == "__main__":
    main()
