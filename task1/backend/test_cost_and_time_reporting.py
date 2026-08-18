"""
Dedicated test for behavior 10 ("a run can report what it spent and
where the time went, stage by stage"). HeuristicClient always reports
cost_cents=0 (no API call was made, which is honest, not a bug) - so
proving the accumulation and reporting mechanism itself needs a client
that reports real, non-zero, varying cost per document.
"""

from pathlib import Path

from app.db import connect
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.state_machine import get_run_status, run_stages, start_run

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data" / "meridian-builders"
PILE_ID = "meridian-builders"


class FakeMeteredClient:
    """Deterministic per-document cost/behavior, standing in for a real
    LLM client so the accumulation math is actually exercised."""

    def __init__(self):
        self.calls = 0

    def understand_document(self, *, text: str, filename: str) -> dict:
        self.calls += 1
        # Distinct, non-round cost per call so an accidental
        # "always returns the same number" bug would be visible.
        return {"doc_type": "unknown", "facts": [], "cost_cents": 0.7 + self.calls}


def test_stage_cost_accumulates_across_documents_and_totals_correctly(tmp_path):
    conn = connect(tmp_path / "state.db")
    client = FakeMeteredClient()

    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    stages = [
        ("ingest", make_ingest_stage(conn, PILE_ID, SAMPLE_DATA_DIR)),
        ("extract_facts", make_extract_facts_stage(conn, PILE_ID, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, PILE_ID)),
        ("build_register", make_build_register_stage(conn, PILE_ID, run_id)),
    ]
    outcomes = run_stages(conn, run_id, stages)

    ingest_count = outcomes[0].result["ingested_count"]
    assert ingest_count == 12  # documented corpus size

    extract_outcome = next(o for o in outcomes if o.stage_name == "extract_facts")
    expected_cost = sum(0.7 + i for i in range(1, ingest_count + 1))
    assert extract_outcome.cost_cents == expected_cost
    assert client.calls == ingest_count  # one real call per document, not batched away

    # Every other stage made no metered calls - their cost is exactly 0,
    # not "unset" or missing.
    ingest_outcome = outcomes[0]
    disagreements_outcome = outcomes[2]
    register_outcome = outcomes[3]
    assert ingest_outcome.cost_cents == 0
    assert disagreements_outcome.cost_cents == 0
    assert register_outcome.cost_cents == 0

    status = get_run_status(conn, run_id)
    # total_cost_cents is the literal sum of every stage's own cost -
    # not a separately-tracked number that could silently drift from it.
    stage_sum = sum(s["cost_cents"] for s in status["stages"])
    assert status["total_cost_cents"] == stage_sum
    assert status["total_cost_cents"] == expected_cost


def test_every_stage_reports_its_own_duration(tmp_path):
    conn = connect(tmp_path / "state.db")
    client = FakeMeteredClient()

    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    stages = [
        ("ingest", make_ingest_stage(conn, PILE_ID, SAMPLE_DATA_DIR)),
        ("extract_facts", make_extract_facts_stage(conn, PILE_ID, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, PILE_ID)),
        ("build_register", make_build_register_stage(conn, PILE_ID, run_id)),
    ]
    run_stages(conn, run_id, stages)

    status = get_run_status(conn, run_id)
    for stage in status["stages"]:
        assert stage["duration_ms"] is not None
        assert stage["duration_ms"] >= 0


def test_resumed_run_does_not_double_count_cost_for_already_completed_stages(tmp_path):
    """A stage that's already 'completed' is skipped on resume - its
    cost must not be added again, and the never-billed skip itself
    costs nothing."""

    conn = connect(tmp_path / "state.db")
    client = FakeMeteredClient()

    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    stages = [
        ("ingest", make_ingest_stage(conn, PILE_ID, SAMPLE_DATA_DIR)),
        ("extract_facts", make_extract_facts_stage(conn, PILE_ID, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, PILE_ID)),
        ("build_register", make_build_register_stage(conn, PILE_ID, run_id)),
    ]
    run_stages(conn, run_id, stages)
    cost_after_first_pass = get_run_status(conn, run_id)["total_cost_cents"]
    calls_after_first_pass = client.calls

    # "Resume" the same completed run with a fresh stage list (same
    # run_id) - every stage should be recognized as already done.
    outcomes_second_pass = run_stages(conn, run_id, stages)

    assert all(o.status == "skipped_already_done" for o in outcomes_second_pass)
    assert client.calls == calls_after_first_pass  # no new metered calls made
    assert get_run_status(conn, run_id)["total_cost_cents"] == cost_after_first_pass
