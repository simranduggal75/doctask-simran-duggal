"""
Movement 3: stay alive.

New documents arrive into a watched location (polled, not a persistent
daemon - consistent with the "no new infrastructure" lean-stack
decision and Task 2's nightly-batch precedent). Each arrival produces a
FOCUSED update: only the new document(s) get ingested and extracted,
only the contract(s) they actually reference get recomputed, and every
vendor row a new document did NOT touch is copied byte-identical from
the previous register version - not silently regenerated from scratch,
which is provable by JSON equality, not just asserted.

If a pile already has an APPROVED version and the new facts contradict
it for an affected vendor, that is logged as a distinct
conflict_detected change_log entry - surfaced, not silently resolved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.facts import build_vendor_row, detect_disagreements_for_contract, load_facts_by_contract
from app.llm_client import UnderstandingClient
from app.state_machine import new_id, now_iso


def _hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_detect_new_documents_stage(conn, pile_id: str, source_dir: Path):
    def detect(context: dict) -> dict:
        new_files = []
        for path in sorted(source_dir.rglob("*.txt")):
            if path.name == "README.md":
                continue
            text = path.read_text(encoding="utf-8")
            content_hash = _hash_content(text)
            existing = conn.execute(
                "SELECT id FROM documents WHERE pile_id = ? AND content_hash = ?",
                (pile_id, content_hash),
            ).fetchone()
            if existing:
                continue
            new_files.append({
                "path": str(path), "filename": path.name,
                "text": text, "content_hash": content_hash,
            })

        if not new_files:
            # Genuinely nothing to do - short-circuits the rest of the
            # run via complete_early rather than doing pointless work
            # in every downstream stage.
            return {"decision": "complete_early", "new_document_count": 0}

        return {"decision": "proceed", "new_document_count": len(new_files), "new_files": new_files}

    return detect


def make_ingest_new_documents_stage(conn, pile_id: str):
    def ingest(context: dict) -> dict:
        new_files = context["results"]["detect_new_documents"]["new_files"]
        ingested = []

        for f in new_files:
            doc_id = new_id()
            conn.execute(
                "INSERT INTO documents (id, pile_id, filename, doc_type, source_path, "
                "content_text, content_hash, ingested_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                (doc_id, pile_id, f["filename"], f["path"], f["text"], f["content_hash"], now_iso()),
            )
            ingested.append({"document_id": doc_id, "filename": f["filename"]})

        return {"decision": "proceed", "ingested": ingested}

    return ingest


def make_extract_new_facts_stage(conn, pile_id: str, run_id: str, client: UnderstandingClient):
    def extract(context: dict) -> dict:
        ingested = context["results"]["ingest_new_documents"]["ingested"]
        affected_contract_nos: set[str] = set()
        total_facts = 0
        total_cost_cents = 0.0

        for doc in ingested:
            doc_id = doc["document_id"]
            filename, text = conn.execute(
                "SELECT filename, content_text FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()

            result = client.understand_document(text=text, filename=filename)
            doc_type = result.get("doc_type", "unknown")
            facts = result.get("facts", [])
            total_cost_cents += result.get("cost_cents", 0.0)
            conn.execute("UPDATE documents SET doc_type = ? WHERE id = ?", (doc_type, doc_id))

            for fact in facts:
                conn.execute(
                    "INSERT INTO facts (id, pile_id, document_id, fact_type, fact_json, "
                    "source_excerpt, extracted_in_run_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(), pile_id, doc_id, fact["fact_type"], json.dumps(fact["fact_json"]),
                        fact.get("source_excerpt", ""), run_id, now_iso(),
                    ),
                )
                total_facts += 1
                if fact["fact_type"] == "contract_no":
                    affected_contract_nos.add(fact["fact_json"]["value"])

        return {
            "decision": "proceed",
            "total_facts": total_facts,
            "affected_contract_nos": sorted(affected_contract_nos),
            "cost_cents": total_cost_cents,
        }

    return extract


def make_recompute_affected_stage(conn, pile_id: str):
    def recompute(context: dict) -> dict:
        affected = context["results"]["extract_new_facts"]["affected_contract_nos"]
        by_contract = load_facts_by_contract(conn, pile_id)

        recomputed_rows = {}
        for contract_no in affected:
            group = by_contract.get(contract_no)
            if not group or not group["contract"]:
                continue

            disagreements = detect_disagreements_for_contract(contract_no, group)
            row, _ = build_vendor_row(contract_no, group, disagreements)
            recomputed_rows[contract_no] = row

        return {"decision": "proceed", "affected_contract_nos": affected, "recomputed_rows": recomputed_rows}

    return recompute


def make_build_incremental_register_stage(conn, pile_id: str, run_id: str):
    def build(context: dict) -> dict:
        recomputed_rows = context["results"]["recompute_affected"]["recomputed_rows"]

        previous = conn.execute(
            "SELECT content_json FROM deliverable_versions WHERE pile_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (pile_id,),
        ).fetchone()
        previous_content = json.loads(previous[0]) if previous else {"vendors": [], "embedded_instructions_detected": []}
        previous_rows_by_contract = {v["contract_no"]: v for v in previous_content.get("vendors", [])}

        latest_approved = conn.execute(
            "SELECT content_json FROM deliverable_versions WHERE pile_id = ? AND status = 'approved' "
            "ORDER BY version_number DESC LIMIT 1",
            (pile_id,),
        ).fetchone()
        approved_rows_by_contract = (
            {v["contract_no"]: v for v in json.loads(latest_approved[0]).get("vendors", [])}
            if latest_approved else {}
        )

        conflicts = []
        for contract_no, new_row in recomputed_rows.items():
            approved_row = approved_rows_by_contract.get(contract_no)
            if approved_row and approved_row != new_row:
                conflicts.append({
                    "contract_no": contract_no,
                    "vendor": new_row["vendor"],
                    "approved_snapshot": approved_row,
                    "new_snapshot": new_row,
                })

        # Untouched rows are copied verbatim (byte-identical JSON) from
        # the previous version - never recomputed, never regenerated.
        merged_rows = dict(previous_rows_by_contract)
        merged_rows.update(recomputed_rows)

        merged_injection_notes = list(previous_content.get("embedded_instructions_detected", []))

        content = {
            "pile_id": pile_id,
            "vendors": sorted(merged_rows.values(), key=lambda r: r["vendor"]),
            "embedded_instructions_detected": merged_injection_notes,
        }
        content_json = json.dumps(content, sort_keys=True)
        content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

        last_version = conn.execute(
            "SELECT MAX(version_number) FROM deliverable_versions WHERE pile_id = ?", (pile_id,)
        ).fetchone()[0]
        version_number = (last_version or 0) + 1

        version_id = new_id()
        conn.execute(
            "INSERT INTO deliverable_versions (id, pile_id, version_number, content_json, "
            "content_hash, change_summary, created_in_run_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                version_id, pile_id, version_number, content_json, content_hash,
                f"Focused update: {len(recomputed_rows)} vendor row(s) recomputed "
                f"({', '.join(recomputed_rows) or 'none'}), "
                f"{len(merged_rows) - len(recomputed_rows)} row(s) unchanged.",
                run_id, now_iso(),
            ),
        )
        conn.execute(
            "INSERT INTO change_log (id, pile_id, event_type, description, run_id, created_at) "
            "VALUES (?, ?, 'deliverable_updated', ?, ?, ?)",
            (
                new_id(), pile_id,
                f"Register version {version_number}: focused update affecting "
                f"{list(recomputed_rows.keys())}.",
                run_id, now_iso(),
            ),
        )
        for conflict in conflicts:
            conn.execute(
                "INSERT INTO change_log (id, pile_id, event_type, description, run_id, created_at) "
                "VALUES (?, ?, 'conflict_detected', ?, ?, ?)",
                (
                    new_id(), pile_id,
                    f"New facts for {conflict['vendor']} ({conflict['contract_no']}) contradict "
                    f"the currently approved register - surfaced for human review, not "
                    f"silently overwritten.",
                    run_id, now_iso(),
                ),
            )

        return {
            "decision": "proceed",
            "version_id": version_id,
            "version_number": version_number,
            "content": content,
            "unchanged_row_count": len(merged_rows) - len(recomputed_rows),
            "conflicts": conflicts,
        }

    return build


def build_incremental_plan(conn, pile_id: str, run_id: str, source_dir: Path, client: UnderstandingClient):
    stage_names = [
        "detect_new_documents", "ingest_new_documents", "extract_new_facts",
        "recompute_affected", "build_incremental_register",
    ]
    stages = [
        ("detect_new_documents", make_detect_new_documents_stage(conn, pile_id, source_dir)),
        ("ingest_new_documents", make_ingest_new_documents_stage(conn, pile_id)),
        ("extract_new_facts", make_extract_new_facts_stage(conn, pile_id, run_id, client)),
        ("recompute_affected", make_recompute_affected_stage(conn, pile_id)),
        ("build_incremental_register", make_build_incremental_register_stage(conn, pile_id, run_id)),
    ]
    return stage_names, stages
