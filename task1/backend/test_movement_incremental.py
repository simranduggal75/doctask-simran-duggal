"""
Offline tests for Movement 3 against a mutable copy of the real
corpus (a new document has to actually arrive after the initial
understand, so the fixed sample_data dir is copied into tmp_path
first). No live model key.
"""

import json
import shutil
from pathlib import Path

from app.db import connect
from app.llm_client import HeuristicClient
from app.movement_incremental import build_incremental_plan
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.state_machine import run_stages, start_run

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data" / "meridian-builders"
PILE_ID = "meridian-builders"


def _copy_corpus(tmp_path) -> Path:
    dest = tmp_path / "corpus"
    shutil.copytree(SAMPLE_DATA_DIR, dest)
    return dest


def _understand(conn, source_dir):
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
    return run_stages(conn, run_id, stages)


def _scan(conn, source_dir):
    run_id = start_run(
        conn, pile_id=PILE_ID, run_type="incremental_update",
        stage_names=[
            "detect_new_documents", "ingest_new_documents", "extract_new_facts",
            "recompute_affected", "build_incremental_register",
        ],
    )
    client = HeuristicClient()
    _, stages = build_incremental_plan(conn, PILE_ID, run_id, source_dir, client)
    return run_id, run_stages(conn, run_id, stages)


def _get_version(conn, version_number):
    row = conn.execute(
        "SELECT content_json FROM deliverable_versions WHERE pile_id = ? AND version_number = ?",
        (PILE_ID, version_number),
    ).fetchone()
    return json.loads(row[0])


NEW_RIDGELINE_INVOICE = """RIDGELINE ELECTRICAL LLC
INVOICE

Invoice No: RE-2026-099
Date: 2026-04-10
Bill To: Meridian Builders GC
Contract No: MB-2026-014

LINE ITEMS
Additional panel testing, Building C Level 2 (per Change Order 1) - 10 hrs @ $110.00/hr
                                                                      $1,100.00

TOTAL DUE: $1,100.00
Payment Terms: Net 30
"""


def test_no_new_documents_short_circuits_without_touching_register(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    versions_before = conn.execute(
        "SELECT COUNT(*) FROM deliverable_versions WHERE pile_id = ?", (PILE_ID,)
    ).fetchone()[0]

    run_id, outcomes = _scan(conn, source_dir)

    assert outcomes[0].status == "completed"
    assert outcomes[0].result["new_document_count"] == 0
    # Every other stage was skipped, never actually ran.
    assert [o.status for o in outcomes[1:]] == ["skipped_by_decision"] * 4

    versions_after = conn.execute(
        "SELECT COUNT(*) FROM deliverable_versions WHERE pile_id = ?", (PILE_ID,)
    ).fetchone()[0]
    assert versions_after == versions_before, "a no-op scan must not create a pointless new version"


def test_new_invoice_only_changes_its_own_vendor_row(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    v1 = _get_version(conn, 1)
    v1_by_vendor = {row["vendor"]: row for row in v1["vendors"]}

    (source_dir / "invoices" / "ridgeline-electrical-invoice-003.txt").write_text(
        NEW_RIDGELINE_INVOICE, encoding="utf-8"
    )

    run_id, outcomes = _scan(conn, source_dir)
    build_result = outcomes[-1].result
    assert build_result["version_number"] == 2

    v2 = _get_version(conn, 2)
    v2_by_vendor = {row["vendor"]: row for row in v2["vendors"]}

    # Byte-identical (not just "looks similar") for every vendor the
    # new document did not touch.
    assert v2_by_vendor["Apex Concrete Co."] == v1_by_vendor["Apex Concrete Co."]
    assert v2_by_vendor["Summit Plumbing Inc."] == v1_by_vendor["Summit Plumbing Inc."]

    # Ridgeline's row DID change - new invoice added to its total.
    assert v2_by_vendor["Ridgeline Electrical LLC"] != v1_by_vendor["Ridgeline Electrical LLC"]
    assert v2_by_vendor["Ridgeline Electrical LLC"]["total_invoiced"] == (
        v1_by_vendor["Ridgeline Electrical LLC"]["total_invoiced"] + 1100.0
    )
    # The new invoice bills at the correct post-amendment rate - no new
    # rate-mismatch disagreement should appear for it.
    ridgeline_disagreement_types = [
        d["type"] for d in v2_by_vendor["Ridgeline Electrical LLC"]["disagreements"]
    ]
    assert ridgeline_disagreement_types.count("rate_mismatch") == 1  # only the original one


def test_recompute_is_scoped_to_the_affected_contract_only(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    (source_dir / "invoices" / "ridgeline-electrical-invoice-003.txt").write_text(
        NEW_RIDGELINE_INVOICE, encoding="utf-8"
    )

    run_id, outcomes = _scan(conn, source_dir)
    recompute_result = outcomes[3].result  # recompute_affected stage
    assert recompute_result["affected_contract_nos"] == ["MB-2026-014"]  # Ridgeline only
    assert list(recompute_result["recomputed_rows"].keys()) == ["MB-2026-014"]


def test_conflict_with_approved_version_is_surfaced_not_silently_overwritten(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    # Simulate what issue #13's approve endpoint will do: mark version 1 approved.
    conn.execute(
        "UPDATE deliverable_versions SET status = 'approved' WHERE pile_id = ? AND version_number = 1",
        (PILE_ID,),
    )

    (source_dir / "invoices" / "ridgeline-electrical-invoice-003.txt").write_text(
        NEW_RIDGELINE_INVOICE, encoding="utf-8"
    )

    run_id, outcomes = _scan(conn, source_dir)
    build_result = outcomes[-1].result

    assert len(build_result["conflicts"]) == 1
    assert build_result["conflicts"][0]["vendor"] == "Ridgeline Electrical LLC"

    conflict_log = conn.execute(
        "SELECT event_type FROM change_log WHERE pile_id = ? AND event_type = 'conflict_detected'",
        (PILE_ID,),
    ).fetchall()
    assert len(conflict_log) == 1

    # The new version still exists and is pending - it was surfaced,
    # not blocked, and not silently substituted for the approved one.
    v2 = conn.execute(
        "SELECT status FROM deliverable_versions WHERE pile_id = ? AND version_number = 2", (PILE_ID,)
    ).fetchone()
    assert v2[0] == "pending"

    approved_still_intact = conn.execute(
        "SELECT status FROM deliverable_versions WHERE pile_id = ? AND version_number = 1", (PILE_ID,)
    ).fetchone()
    assert approved_still_intact[0] == "approved"


def test_change_log_answers_what_changed_when_and_why(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    (source_dir / "invoices" / "ridgeline-electrical-invoice-003.txt").write_text(
        NEW_RIDGELINE_INVOICE, encoding="utf-8"
    )
    run_id, outcomes = _scan(conn, source_dir)

    rows = conn.execute(
        "SELECT event_type, description, run_id FROM change_log WHERE pile_id = ? ORDER BY created_at",
        (PILE_ID,),
    ).fetchall()

    assert len(rows) >= 2  # at least the understand-run's + this scan's entries
    last = rows[-1]
    assert last[2] == run_id  # traceable to the exact run that made the change
    assert "Ridgeline" in last[1] or "MB-2026-014" in last[1]


def test_ingestion_still_idempotent_across_scans(tmp_path):
    conn = connect(tmp_path / "state.db")
    source_dir = _copy_corpus(tmp_path)
    _understand(conn, source_dir)

    (source_dir / "invoices" / "ridgeline-electrical-invoice-003.txt").write_text(
        NEW_RIDGELINE_INVOICE, encoding="utf-8"
    )
    _scan(conn, source_dir)

    doc_count_after_first_scan = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    # Scanning again with no further new files must find nothing.
    run_id, outcomes = _scan(conn, source_dir)
    assert outcomes[0].result["new_document_count"] == 0

    doc_count_after_second_scan = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count_after_second_scan == doc_count_after_first_scan
