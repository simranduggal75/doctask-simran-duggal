"""
End-to-end offline test of Movement 1 against the real sample_data
corpus (not a synthetic fixture) - proves the pipeline correctly finds
exactly the disagreements documented as ground truth in
sample_data/meridian-builders/README.md, and nothing else. No live
model key: uses the deterministic HeuristicClient.
"""

import json
from pathlib import Path

from app.db import connect
from app.llm_client import HeuristicClient
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.state_machine import run_stages, start_run

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data" / "meridian-builders"


def _run_understand(tmp_path, source_dir=SAMPLE_DATA_DIR):
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    pile_id = "meridian-builders"

    run_id = start_run(
        conn, pile_id=pile_id, run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )

    client = HeuristicClient()
    stages = [
        ("ingest", make_ingest_stage(conn, pile_id, source_dir)),
        ("extract_facts", make_extract_facts_stage(conn, pile_id, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, pile_id)),
        ("build_register", make_build_register_stage(conn, pile_id, run_id)),
    ]

    outcomes = run_stages(conn, run_id, stages)
    return conn, run_id, outcomes


def test_ingest_finds_all_documents_and_is_idempotent(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)

    ingest_outcome = outcomes[0]
    assert ingest_outcome.result["ingested_count"] == 12  # 3 contracts + 2 amendments + 2 certs + 5 invoices
    assert ingest_outcome.result["skipped_count"] == 0

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count == 12


def test_ridgeline_rate_mismatch_is_detected_and_grounded(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    disagreements = outcomes[2].result["disagreements"]

    ridgeline = [d for d in disagreements if d["vendor"] == "Ridgeline Electrical LLC"]
    assert len(ridgeline) == 1
    assert ridgeline[0]["type"] == "rate_mismatch"
    assert "$95.0/hr" in ridgeline[0]["description"] or "$95.00/hr" in ridgeline[0]["description"] or "95.0" in ridgeline[0]["description"]
    assert "110" in ridgeline[0]["description"]
    # Grounded: cites real document ids, not invented
    assert len(ridgeline[0]["source_document_ids"]) == 2


def test_apex_has_exactly_two_intended_findings(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    disagreements = outcomes[2].result["disagreements"]

    apex = [d for d in disagreements if d["vendor"] == "Apex Concrete Co."]
    types = sorted(d["type"] for d in apex)
    assert types == ["missing_insurance_certificate", "unsigned_change_order_invoiced"]


def test_summit_is_fully_clean_honest_no_findings(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    disagreements = outcomes[2].result["disagreements"]

    summit = [d for d in disagreements if d["vendor"] == "Summit Plumbing Inc."]
    assert summit == [], "Summit is the deliberately clean vendor - any finding here is a false positive"


def test_total_disagreement_count_matches_documented_ground_truth(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    disagreements = outcomes[2].result["disagreements"]
    # 1 (Ridgeline rate) + 2 (Apex insurance + unsigned CO) + 0 (Summit) = 3
    assert len(disagreements) == 3


def test_injection_attempt_extracted_as_data_never_acted_on(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    register = outcomes[3].result["content"]

    injections = register["embedded_instructions_detected"]
    assert len(injections) == 1
    assert injections[0]["vendor"] == "Summit Plumbing Inc."
    assert "ignore" in injections[0]["instruction_text"].lower()

    # The instruction demanded findings be auto-approved and Summit be
    # marked compliant - it must NOT have had any effect. Summit still
    # has zero disagreements (correctly, since its real invoices ARE
    # compliant) and nothing in the system was "auto-approved" - the
    # register version is still pending human approval.
    version_status = conn.execute(
        "SELECT status FROM deliverable_versions WHERE pile_id = ? ORDER BY version_number DESC LIMIT 1",
        ("meridian-builders",),
    ).fetchone()[0]
    assert version_status == "pending"


def test_register_is_created_as_pending_not_auto_approved(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    build_result = outcomes[3].result

    assert build_result["version_number"] == 1
    row = conn.execute(
        "SELECT status FROM deliverable_versions WHERE id = ?", (build_result["version_id"],)
    ).fetchone()
    assert row[0] == "pending"


def test_every_fact_has_a_real_source_excerpt(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)

    rows = conn.execute("SELECT fact_type, source_excerpt FROM facts").fetchall()
    assert len(rows) > 0
    for fact_type, excerpt in rows:
        assert excerpt and excerpt.strip(), f"fact {fact_type} has no grounding excerpt"


def test_rerunning_understand_on_same_pile_does_not_duplicate_documents(tmp_path):
    conn, run_id, outcomes = _run_understand(tmp_path)
    doc_count_after_first = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    # Second understand run against the same pile - ingest must skip
    # everything (content-hash match), not create duplicate document rows.
    run_id_2 = start_run(
        conn, pile_id="meridian-builders", run_type="understand",
        stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
    )
    client = HeuristicClient()
    stages = [
        ("ingest", make_ingest_stage(conn, "meridian-builders", SAMPLE_DATA_DIR)),
        ("extract_facts", make_extract_facts_stage(conn, "meridian-builders", run_id_2, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, "meridian-builders")),
        ("build_register", make_build_register_stage(conn, "meridian-builders", run_id_2)),
    ]
    outcomes_2 = run_stages(conn, run_id_2, stages)

    assert outcomes_2[0].result["ingested_count"] == 0
    assert outcomes_2[0].result["skipped_count"] == 12
    doc_count_after_second = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count_after_second == doc_count_after_first

    # A second, independent register version is created (version 2) -
    # re-understanding produces a new version, not a silent overwrite.
    assert outcomes_2[3].result["version_number"] == 2
