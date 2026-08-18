"""
Movement 1: understand the pile.

Four stages, run through the state machine in state_machine.py:
    ingest -> extract_facts -> detect_disagreements -> build_register

Every stage is idempotent and grounded: ingest never re-imports an
unchanged document (content-hash keyed), extraction never records a
fact without the literal source text it came from, and disagreement
detection only ever compares facts that were actually extracted -
nothing here invents a number.

The register stage's output lands as a *pending* deliverable_versions
row - approval is a separate, explicit human action (issue #13's
scope), not something this pipeline does to itself.
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


def make_ingest_stage(conn, pile_id: str, source_dir: Path):
    def ingest(context: dict) -> dict:
        ingested, skipped = [], []

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
                skipped.append({"filename": path.name, "document_id": existing[0]})
                continue

            doc_id = new_id()
            conn.execute(
                "INSERT INTO documents (id, pile_id, filename, doc_type, source_path, "
                "content_text, content_hash, ingested_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                (doc_id, pile_id, path.name, str(path), text, content_hash, now_iso()),
            )
            ingested.append({"filename": path.name, "document_id": doc_id})

        return {
            "decision": "proceed",
            "ingested_count": len(ingested),
            "skipped_count": len(skipped),
            "ingested": ingested,
            "skipped": skipped,
        }

    return ingest


def make_extract_facts_stage(conn, pile_id: str, run_id: str, client: UnderstandingClient):
    def extract(context: dict) -> dict:
        doc_rows = conn.execute(
            "SELECT id, filename, content_text FROM documents WHERE pile_id = ?",
            (pile_id,),
        ).fetchall()

        total_facts = 0
        per_document = []

        for doc_id, filename, text in doc_rows:
            result = client.understand_document(text=text, filename=filename)
            doc_type = result.get("doc_type", "unknown")
            facts = result.get("facts", [])

            conn.execute("UPDATE documents SET doc_type = ? WHERE id = ?", (doc_type, doc_id))

            # Idempotent within this run_id: don't duplicate facts if this
            # document was already processed earlier in the same run.
            conn.execute(
                "DELETE FROM facts WHERE document_id = ? AND extracted_in_run_id = ?",
                (doc_id, run_id),
            )

            for fact in facts:
                conn.execute(
                    "INSERT INTO facts (id, pile_id, document_id, fact_type, fact_json, "
                    "source_excerpt, extracted_in_run_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(), pile_id, doc_id, fact["fact_type"],
                        json.dumps(fact["fact_json"]), fact.get("source_excerpt", ""),
                        run_id, now_iso(),
                    ),
                )
                total_facts += 1

            per_document.append({"document_id": doc_id, "filename": filename, "doc_type": doc_type, "fact_count": len(facts)})

        return {"decision": "proceed", "documents_processed": len(doc_rows), "total_facts": total_facts, "per_document": per_document}

    return extract


def make_detect_disagreements_stage(conn, pile_id: str):
    def detect(context: dict) -> dict:
        by_contract = load_facts_by_contract(conn, pile_id)
        disagreements = []

        for contract_no, group in by_contract.items():
            if not group["contract"]:
                continue
            disagreements.extend(detect_disagreements_for_contract(contract_no, group))

        return {"decision": "proceed", "disagreements": disagreements}

    return detect


def make_build_register_stage(conn, pile_id: str, run_id: str):
    def build(context: dict) -> dict:
        disagreements = context["results"]["detect_disagreements"]["disagreements"]
        by_contract = load_facts_by_contract(conn, pile_id)

        disagreements_by_contract: dict[str, list] = {}
        for d in disagreements:
            disagreements_by_contract.setdefault(d["contract_no"], []).append(d)

        vendor_rows = []
        injection_notes = []

        for contract_no, group in by_contract.items():
            if not group["contract"]:
                continue

            row, notes = build_vendor_row(
                contract_no, group, disagreements_by_contract.get(contract_no, [])
            )
            vendor_rows.append(row)
            injection_notes.extend(notes)

        content = {
            "pile_id": pile_id,
            "vendors": sorted(vendor_rows, key=lambda r: r["vendor"]),
            "embedded_instructions_detected": injection_notes,
        }
        content_json = json.dumps(content, sort_keys=True)
        content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

        last_version = conn.execute(
            "SELECT MAX(version_number) FROM deliverable_versions WHERE pile_id = ?",
            (pile_id,),
        ).fetchone()[0]
        version_number = (last_version or 0) + 1

        version_id = new_id()
        conn.execute(
            "INSERT INTO deliverable_versions (id, pile_id, version_number, content_json, "
            "content_hash, change_summary, created_in_run_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                version_id, pile_id, version_number, content_json, content_hash,
                f"Initial understanding: {len(vendor_rows)} vendors, "
                f"{len(disagreements)} disagreement(s) found.",
                run_id, now_iso(),
            ),
        )
        conn.execute(
            "INSERT INTO change_log (id, pile_id, event_type, description, run_id, created_at) "
            "VALUES (?, ?, 'deliverable_updated', ?, ?, ?)",
            (
                new_id(), pile_id,
                f"Register version {version_number} created, pending approval.",
                run_id, now_iso(),
            ),
        )

        return {
            "decision": "proceed",
            "version_id": version_id,
            "version_number": version_number,
            "content": content,
        }

    return build
