"""
Shared read-side helper: groups a pile's extracted facts by contract,
by document type. Used by both Movement 1 (disagreement detection,
register building) and Movement 2 (rule checking) so both read the
same facts the same way rather than each re-deriving grouping logic
that could quietly drift apart.
"""

from __future__ import annotations

import json


def load_facts_by_contract(conn, pile_id: str) -> dict:
    rows = conn.execute(
        "SELECT f.document_id, d.doc_type, d.filename, f.fact_type, f.fact_json, f.source_excerpt "
        "FROM facts f JOIN documents d ON f.document_id = d.id WHERE f.pile_id = ? "
        "ORDER BY f.created_at",
        (pile_id,),
    ).fetchall()

    doc_facts: dict[str, dict] = {}
    doc_meta: dict[str, dict] = {}

    for document_id, doc_type, filename, fact_type, fact_json, excerpt in rows:
        doc_meta[document_id] = {"doc_type": doc_type, "filename": filename}
        doc_facts.setdefault(document_id, {}).setdefault(fact_type, []).append(
            (json.loads(fact_json), excerpt)
        )

    by_contract: dict[str, dict] = {}
    for document_id, facts_by_type in doc_facts.items():
        contract_no_facts = facts_by_type.get("contract_no")
        if not contract_no_facts:
            continue
        contract_no = contract_no_facts[0][0]["value"]
        group = by_contract.setdefault(
            contract_no, {"contract": None, "amendments": [], "invoices": [], "certificates": []}
        )
        entry = {
            "document_id": document_id,
            "filename": doc_meta[document_id]["filename"],
            "facts": facts_by_type,
        }
        doc_type = doc_meta[document_id]["doc_type"]
        if doc_type == "contract":
            group["contract"] = entry
        elif doc_type == "amendment":
            group["amendments"].append(entry)
        elif doc_type == "invoice":
            group["invoices"].append(entry)
        elif doc_type == "certificate":
            group["certificates"].append(entry)

    return by_contract
