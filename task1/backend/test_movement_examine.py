"""
Offline tests for Movement 2 against the REAL sample corpus. The core
claim under test is genuine rule-drivenness: two different playbooks
must produce two different result sets, not the same hardcoded checks
relabeled. No live model key - rule classification is pattern-based.
"""

from pathlib import Path

from app.db import connect
from app.llm_client import HeuristicClient
from app.movement_examine import build_examine_plan, load_active_rules
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.state_machine import new_id, now_iso, run_stages, start_run

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data" / "meridian-builders"
PILE_ID = "meridian-builders"


def _understand(conn, source_dir=SAMPLE_DATA_DIR):
    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    client = HeuristicClient()
    stages = [
        ("ingest", make_ingest_stage(conn, PILE_ID, source_dir)),
        ("extract_facts", make_extract_facts_stage(conn, PILE_ID, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, PILE_ID)),
        ("build_register", make_build_register_stage(conn, PILE_ID, run_id)),
    ]
    run_stages(conn, run_id, stages)


def _add_rule(conn, rule_text: str) -> str:
    rule_id = new_id()
    conn.execute(
        "INSERT INTO rules (id, pile_id, rule_text, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (rule_id, PILE_ID, rule_text, now_iso()),
    )
    return rule_id


def _examine(conn):
    stage_names, stages = build_examine_plan(conn, PILE_ID, "placeholder")
    rule_ids = [n.replace("check_rule_", "") for n in stage_names if n != "compile_findings"]
    run_id = start_run(conn, pile_id=PILE_ID, run_type="examine", stage_names=stage_names)
    _, stages = build_examine_plan(conn, PILE_ID, run_id)
    outcomes = run_stages(conn, run_id, stages)
    return run_id, outcomes


def test_small_playbook_produces_only_its_own_findings(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.")

    run_id, outcomes = _examine(conn)
    compile_result = outcomes[-1].result

    assert compile_result["findings_created"] == 1
    assert compile_result["findings"][0]["title"].startswith("No insurance certificate on file")
    assert "Apex" in compile_result["findings"][0]["title"]


def test_different_playbook_produces_different_findings(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "Change orders must be signed by both parties before an invoice may reflect them.")
    _add_rule(conn, "Total amounts invoiced against a contract must never exceed its current not-to-exceed amount.")

    run_id, outcomes = _examine(conn)
    compile_result = outcomes[-1].result

    titles = sorted(f["title"] for f in compile_result["findings"])
    # Insurance rule was NOT included this time - its finding must not appear.
    assert not any("insurance" in t.lower() for t in titles)
    # The signed-change-order rule finding must appear.
    assert any("Unsigned change order" in t for t in titles)
    # The NTE rule finds nothing in this corpus (no vendor is over budget) -
    # an honest zero, not a fabricated finding.
    assert not any("Not-to-exceed" in t for t in titles)


def test_full_playbook_matches_movement_1_disagreement_count(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.")
    _add_rule(conn, "Change orders must be signed by both parties before an invoice may reflect them.")
    _add_rule(conn, "Invoiced rates must match the current, most-recently amended contract terms.")

    run_id, outcomes = _examine(conn)
    compile_result = outcomes[-1].result

    # Same 3 findings Movement 1's disagreement detection found, via an
    # entirely independent, user-supplied-rule-driven code path.
    assert compile_result["findings_created"] == 3


def test_unrecognized_rule_is_reported_honestly_not_silently_dropped(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "All subcontractors must wear branded safety vests on site at all times.")

    run_id, outcomes = _examine(conn)
    compile_result = outcomes[-1].result

    assert compile_result["findings_created"] == 0
    assert len(compile_result["unsupported_rules"]) == 1
    assert "safety vests" in compile_result["unsupported_rules"][0]["rule_text"]


def test_clean_playbook_against_clean_vendor_scope_is_honest_no_findings(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.")

    # Findings must be scoped to a real vendor (Apex) - Summit, which
    # has a certificate on file, must not appear in any finding.
    run_id, outcomes = _examine(conn)
    compile_result = outcomes[-1].result
    for finding in compile_result["findings"]:
        assert "Summit" not in finding["title"]


def test_findings_are_grounded_and_persisted_as_pending(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    _add_rule(conn, "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.")

    run_id, outcomes = _examine(conn)

    rows = conn.execute("SELECT status, source_document_ids FROM findings").fetchall()
    assert len(rows) == 1
    status, source_document_ids = rows[0]
    assert status == "pending"
    assert source_document_ids and source_document_ids != "[]"


def test_examine_stages_are_genuinely_dynamic_per_rule(tmp_path):
    conn = connect(tmp_path / "state.db")
    _understand(conn)
    r1 = _add_rule(conn, "Every subcontractor must have a Certificate of Insurance on file before any invoice is processed.")
    r2 = _add_rule(conn, "Change orders must be signed by both parties before an invoice may reflect them.")

    stage_names, _ = build_examine_plan(conn, PILE_ID, "run-x")
    assert stage_names == [f"check_rule_{r1}", f"check_rule_{r2}", "compile_findings"]
