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


def detect_disagreements_for_contract(contract_no: str, group: dict) -> list[dict]:
    """
    Pure computation: internal-consistency check for one contract's
    group (contract + amendments + invoices + certificates, as
    produced by load_facts_by_contract). Only *signed* amendments are
    treated as changing effective terms. Shared by Movement 1 (runs
    this for every contract in the pile) and Movement 3 (runs it only
    for contracts a newly-arrived document actually references) so a
    fix here applies consistently to both instead of silently drifting
    apart across two copies.
    """

    contract = group["contract"]
    vendor_facts = contract["facts"].get("vendor")
    vendor_name = vendor_facts[0][0]["value"] if vendor_facts else contract_no

    base_rate_facts = contract["facts"].get("out_of_scope_hourly_rate")
    base_rate = base_rate_facts[0][0]["value"] if base_rate_facts else None

    insurance_facts = contract["facts"].get("insurance_certificate_required")
    insurance_required = bool(insurance_facts and insurance_facts[0][0]["value"])

    rate_changes = []
    for amendment in group["amendments"]:
        rate_facts = amendment["facts"].get("revised_hourly_rate")
        sig_facts = amendment["facts"].get("signature_status")
        fully_signed = bool(
            sig_facts and sig_facts[0][0].get("vendor_signed") and sig_facts[0][0].get("gc_signed")
        )
        if rate_facts:
            rc = rate_facts[0][0]
            rate_changes.append({"to": rc["to"], "effective_date": rc["effective_date"], "signed": fully_signed})

    disagreements = []

    if insurance_required and not group["certificates"]:
        disagreements.append({
            "contract_no": contract_no,
            "vendor": vendor_name,
            "type": "missing_insurance_certificate",
            "description": (
                f"Contract requires a Certificate of Insurance on file before "
                f"invoicing, but none exists in the pile for {vendor_name}."
            ),
            "source_document_ids": [contract["document_id"]] + [
                inv["document_id"] for inv in group["invoices"]
            ],
        })

    for invoice in group["invoices"]:
        invoice_date_facts = invoice["facts"].get("invoice_date")
        invoice_date = invoice_date_facts[0][0]["value"] if invoice_date_facts else None

        for fact_json, excerpt in invoice["facts"].get("invoice_line_item", []):
            if fact_json.get("references_change_order"):
                for amendment in group["amendments"]:
                    sig_facts = amendment["facts"].get("signature_status")
                    if sig_facts and not sig_facts[0][0].get("vendor_signed"):
                        disagreements.append({
                            "contract_no": contract_no,
                            "vendor": vendor_name,
                            "type": "unsigned_change_order_invoiced",
                            "description": (
                                f"Invoice line item references a change order that "
                                f"is not yet signed by the subcontractor: {excerpt}"
                            ),
                            "source_document_ids": [invoice["document_id"], amendment["document_id"]],
                        })

            if "rate_used" in fact_json and invoice_date:
                applicable = base_rate
                for rc in sorted(
                    (r for r in rate_changes if r["signed"]), key=lambda r: r["effective_date"]
                ):
                    if rc["effective_date"] <= invoice_date:
                        applicable = rc["to"]
                if applicable is not None and fact_json["rate_used"] != applicable:
                    disagreements.append({
                        "contract_no": contract_no,
                        "vendor": vendor_name,
                        "type": "rate_mismatch",
                        "description": (
                            f"Invoice bills at ${fact_json['rate_used']}/hr but the "
                            f"applicable rate as of {invoice_date} is ${applicable}/hr "
                            f"per contract/amendment terms."
                        ),
                        "source_document_ids": [invoice["document_id"], contract["document_id"]],
                    })

    return disagreements


def build_vendor_row(contract_no: str, group: dict, disagreements: list) -> tuple[dict, list]:
    """
    Pure computation: one contract's group (as produced by
    load_facts_by_contract) plus its disagreements -> the register row
    for that vendor, plus any embedded-instruction notes found in its
    invoices. Shared by Movement 1 (builds every row) and Movement 3
    (rebuilds only the rows a new document actually affects) so both
    produce byte-identical output for the same inputs.
    """

    contract = group["contract"]
    vendor_facts = contract["facts"].get("vendor")
    vendor_name = vendor_facts[0][0]["value"] if vendor_facts else contract_no

    nte = contract["facts"].get("not_to_exceed_amount")
    current_nte = nte[0][0]["value"] if nte else None
    for amendment in group["amendments"]:
        revised = amendment["facts"].get("revised_not_to_exceed_amount")
        sig = amendment["facts"].get("signature_status")
        if revised and sig and sig[0][0].get("vendor_signed") and sig[0][0].get("gc_signed"):
            current_nte = revised[0][0]["to"]

    total_invoiced = 0.0
    injection_notes = []
    for invoice in group["invoices"]:
        total_facts = invoice["facts"].get("total_due")
        if total_facts:
            total_invoiced += total_facts[0][0]["value"]
        for fact_json, _ in invoice["facts"].get("embedded_instruction_to_system", []):
            injection_notes.append({
                "contract_no": contract_no,
                "vendor": vendor_name,
                "document_id": invoice["document_id"],
                "filename": invoice["filename"],
                "instruction_text": fact_json["text"],
            })

    vendor_row = {
        "contract_no": contract_no,
        "vendor": vendor_name,
        "source_document_id": contract["document_id"],
        "current_not_to_exceed_amount": current_nte,
        "amendment_count": len(group["amendments"]),
        "invoice_count": len(group["invoices"]),
        "total_invoiced": round(total_invoiced, 2),
        "disagreements": disagreements,
    }
    return vendor_row, injection_notes
