"""
Offline tests for the human approval gate: floor behavior 3 (item-by-
item review, rejecting one never discards the rest) and floor behavior
4 (a full flow drivable with no UI, only REST-shaped calls - here
exercised directly against the FastAPI app functions/DB rather than
over real HTTP, since that's what's actually under test: the decision
logic, not the transport).
"""

from pathlib import Path

from app.db import connect
from app.llm_client import HeuristicClient
from app.movement_examine import build_examine_plan
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.routers.approvals import (
    DecisionRequest,
    ReviewFindingsRequest,
    FindingDecision,
    approve_register_version,
    reject_register_version,
    review_findings,
)
from app.state_machine import new_id, now_iso, run_stages, start_run

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data" / "meridian-builders"
PILE_ID = "meridian-builders"


def _seed(conn, db_path, monkeypatch):
    # approvals.py connects via app.config.DB_PATH - point it at this test's db.
    import app.routers.approvals as approvals_module
    monkeypatch.setattr(approvals_module, "DB_PATH", db_path)

    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    client = HeuristicClient()
    stages = [
        ("ingest", make_ingest_stage(conn, PILE_ID, SAMPLE_DATA_DIR)),
        ("extract_facts", make_extract_facts_stage(conn, PILE_ID, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, PILE_ID)),
        ("build_register", make_build_register_stage(conn, PILE_ID, run_id)),
    ]
    run_stages(conn, run_id, stages)

    # Two rules -> two findings (Apex's two issues), for the batch-review tests.
    rule_ids = []
    for rule_text in [
        "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.",
        "Change orders must be signed by both parties before an invoice may reflect them.",
    ]:
        rule_id = new_id()
        conn.execute(
            "INSERT INTO rules (id, pile_id, rule_text, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (rule_id, PILE_ID, rule_text, now_iso()),
        )
        rule_ids.append(rule_id)

    examine_run_id = start_run(
        conn, pile_id=PILE_ID, run_type="examine",
        stage_names=[f"check_rule_{r}" for r in rule_ids] + ["compile_findings"],
    )
    _, examine_stages = build_examine_plan(conn, PILE_ID, examine_run_id)
    run_stages(conn, examine_run_id, examine_stages)

    findings = conn.execute("SELECT id FROM findings WHERE pile_id = ?", (PILE_ID,)).fetchall()
    return [f[0] for f in findings]


def test_approve_and_reject_findings_in_one_batch_apply_independently(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    finding_ids = _seed(conn, db_path, monkeypatch)
    assert len(finding_ids) == 2

    result = review_findings(PILE_ID, ReviewFindingsRequest(decisions=[
        FindingDecision(finding_id=finding_ids[0], decision="approve"),
        FindingDecision(finding_id=finding_ids[1], decision="reject", reason="not applicable this cycle"),
    ]))

    assert result["decisions_applied"] == 2
    statuses = {r["finding_id"]: r["status"] for r in result["results"]}
    assert statuses[finding_ids[0]] == "approved"
    assert statuses[finding_ids[1]] == "rejected"

    # Persisted, not just returned.
    rows = dict(conn.execute("SELECT id, status FROM findings").fetchall())
    assert rows[finding_ids[0]] == "approved"
    assert rows[finding_ids[1]] == "rejected"


def test_one_bad_finding_id_in_a_batch_does_not_block_the_others(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    finding_ids = _seed(conn, db_path, monkeypatch)

    result = review_findings(PILE_ID, ReviewFindingsRequest(decisions=[
        FindingDecision(finding_id="does-not-exist", decision="approve"),
        FindingDecision(finding_id=finding_ids[0], decision="approve"),
    ]))

    assert result["decisions_applied"] == 1
    by_id = {r["finding_id"]: r for r in result["results"]}
    assert by_id["does-not-exist"]["applied"] is False
    assert by_id[finding_ids[0]]["applied"] is True
    assert by_id[finding_ids[0]]["status"] == "approved"


def test_already_decided_finding_is_not_silently_redecided(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    finding_ids = _seed(conn, db_path, monkeypatch)

    review_findings(PILE_ID, ReviewFindingsRequest(decisions=[
        FindingDecision(finding_id=finding_ids[0], decision="approve"),
    ]))

    # Try to flip it to rejected in a later call.
    second = review_findings(PILE_ID, ReviewFindingsRequest(decisions=[
        FindingDecision(finding_id=finding_ids[0], decision="reject"),
    ]))

    assert second["decisions_applied"] == 0
    assert second["results"][0]["applied"] is False
    assert second["results"][0]["existing_status"] == "approved"

    # Status genuinely unchanged - the second call had no silent effect.
    status = conn.execute("SELECT status FROM findings WHERE id = ?", (finding_ids[0],)).fetchone()[0]
    assert status == "approved"


def test_register_version_approve_and_reject(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    _seed(conn, db_path, monkeypatch)

    result = approve_register_version(PILE_ID, 1, DecisionRequest(reason="looks correct"))
    assert result["status"] == "approved"

    status = conn.execute(
        "SELECT status FROM deliverable_versions WHERE pile_id = ? AND version_number = 1", (PILE_ID,)
    ).fetchone()[0]
    assert status == "approved"


def test_approving_an_already_decided_version_is_rejected_not_silent(tmp_path, monkeypatch):
    from fastapi import HTTPException

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    _seed(conn, db_path, monkeypatch)

    approve_register_version(PILE_ID, 1, DecisionRequest())

    try:
        reject_register_version(PILE_ID, 1, DecisionRequest())
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409

    status = conn.execute(
        "SELECT status FROM deliverable_versions WHERE pile_id = ? AND version_number = 1", (PILE_ID,)
    ).fetchone()[0]
    assert status == "approved"  # unchanged by the rejected reject attempt


def test_unknown_version_number_is_404(tmp_path, monkeypatch):
    from fastapi import HTTPException

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    _seed(conn, db_path, monkeypatch)

    try:
        approve_register_version(PILE_ID, 999, DecisionRequest())
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_decisions_are_logged_to_change_log(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    finding_ids = _seed(conn, db_path, monkeypatch)

    approve_register_version(PILE_ID, 1, DecisionRequest(decided_by="reviewer-1"))
    review_findings(PILE_ID, ReviewFindingsRequest(
        decisions=[FindingDecision(finding_id=finding_ids[0], decision="approve")],
        decided_by="reviewer-1",
    ))

    events = conn.execute(
        "SELECT event_type FROM change_log WHERE pile_id = ? ORDER BY created_at", (PILE_ID,)
    ).fetchall()
    event_types = [e[0] for e in events]
    assert "version_approved" in event_types
    assert "finding_approved" in event_types
