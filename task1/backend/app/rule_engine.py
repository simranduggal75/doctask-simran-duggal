"""
Rule-driven checking for Movement 2 ("examine"). Rules are plain text
supplied by the user at runtime (stored in the `rules` table), not
hardcoded business logic - the same checker functions below get
invoked only when a rule's text is classified as matching that
checker's pattern. A different playbook (different rule text) produces
a different set of findings; a rule matching no known pattern is
reported honestly as unsupported, never silently dropped or faked.

Deliberately independent from movement_understand.py's disagreement
detection rather than refactored to share it - Movement 1's checks are
a fixed, always-on baseline (internal consistency of the pile), while
these are user-configurable policy checks (external rules) that happen
to examine similar underlying facts. Keeping them separate avoids
touching already-merged, tested code under deadline pressure at the
cost of some logic overlap - a deliberate trade-off, not an oversight.
"""

from __future__ import annotations

import re

from app.facts import load_facts_by_contract


def _matches_all(rule_text: str, *keyword_groups: list[str]) -> bool:
    """Every group must have at least one keyword present (case-insensitive)."""

    lowered = rule_text.lower()
    return all(any(kw in lowered for kw in group) for group in keyword_groups)


def classify_rule(rule_text: str) -> list[str]:
    matched = []

    if _matches_all(rule_text, ["insurance"], ["certificate", "cert"], ["file", "on file"]):
        matched.append("insurance_certificate_on_file")

    if _matches_all(rule_text, ["change order", "amendment"], ["sign"]):
        matched.append("change_order_signed_before_invoicing")

    if _matches_all(rule_text, ["rate", "payment terms", "payment term"], ["match", "current", "reflect", "amend"]):
        matched.append("invoice_matches_current_terms")

    if _matches_all(rule_text, ["not-to-exceed", "not to exceed", "nte", "exceed"]):
        matched.append("not_to_exceed_not_breached")

    return matched


CHECKER_DISPLAY_NAMES = {
    "insurance_certificate_on_file": "Insurance certificate on file",
    "change_order_signed_before_invoicing": "Change order signed before invoicing",
    "invoice_matches_current_terms": "Invoice matches current contract terms",
    "not_to_exceed_not_breached": "Not-to-exceed amount not breached",
}


def check_insurance_certificate_on_file(by_contract: dict) -> list[dict]:
    findings = []
    for contract_no, group in by_contract.items():
        contract = group["contract"]
        if not contract:
            continue
        required = contract["facts"].get("insurance_certificate_required")
        if required and required[0][0]["value"] and not group["certificates"]:
            vendor = contract["facts"].get("vendor")
            vendor_name = vendor[0][0]["value"] if vendor else contract_no
            findings.append({
                "title": f"No insurance certificate on file: {vendor_name}",
                "description": (
                    f"Contract {contract_no} requires a Certificate of Insurance on file "
                    f"before invoicing, but none exists in the pile for {vendor_name}."
                ),
                "severity": "high",
                "source_document_ids": [contract["document_id"]] + [i["document_id"] for i in group["invoices"]],
            })
    return findings


def check_change_order_signed_before_invoicing(by_contract: dict) -> list[dict]:
    findings = []
    for contract_no, group in by_contract.items():
        vendor = (group["contract"]["facts"].get("vendor") if group["contract"] else None)
        vendor_name = vendor[0][0]["value"] if vendor else contract_no

        for invoice in group["invoices"]:
            for fact_json, excerpt in invoice["facts"].get("invoice_line_item", []):
                if not fact_json.get("references_change_order"):
                    continue
                for amendment in group["amendments"]:
                    sig = amendment["facts"].get("signature_status")
                    if sig and not sig[0][0].get("vendor_signed"):
                        findings.append({
                            "title": f"Unsigned change order invoiced: {vendor_name}",
                            "description": (
                                f"Invoice line item references a change order not yet "
                                f"signed by the subcontractor: {excerpt}"
                            ),
                            "severity": "high",
                            "source_document_ids": [invoice["document_id"], amendment["document_id"]],
                        })
    return findings


def check_invoice_matches_current_terms(by_contract: dict) -> list[dict]:
    findings = []
    for contract_no, group in by_contract.items():
        contract = group["contract"]
        if not contract:
            continue
        vendor = contract["facts"].get("vendor")
        vendor_name = vendor[0][0]["value"] if vendor else contract_no

        base_rate_facts = contract["facts"].get("out_of_scope_hourly_rate")
        base_rate = base_rate_facts[0][0]["value"] if base_rate_facts else None

        rate_changes = []
        for amendment in group["amendments"]:
            rate_facts = amendment["facts"].get("revised_hourly_rate")
            sig = amendment["facts"].get("signature_status")
            fully_signed = bool(sig and sig[0][0].get("vendor_signed") and sig[0][0].get("gc_signed"))
            if rate_facts and fully_signed:
                rc = rate_facts[0][0]
                rate_changes.append({"to": rc["to"], "effective_date": rc["effective_date"]})

        for invoice in group["invoices"]:
            invoice_date_facts = invoice["facts"].get("invoice_date")
            invoice_date = invoice_date_facts[0][0]["value"] if invoice_date_facts else None
            if not invoice_date:
                continue

            for fact_json, excerpt in invoice["facts"].get("invoice_line_item", []):
                if "rate_used" not in fact_json:
                    continue
                applicable = base_rate
                for rc in sorted(rate_changes, key=lambda r: r["effective_date"]):
                    if rc["effective_date"] <= invoice_date:
                        applicable = rc["to"]
                if applicable is not None and fact_json["rate_used"] != applicable:
                    findings.append({
                        "title": f"Invoice rate does not match current contract terms: {vendor_name}",
                        "description": (
                            f"Invoice bills at ${fact_json['rate_used']}/hr but the rate "
                            f"applicable as of {invoice_date} is ${applicable}/hr per "
                            f"contract/amendment terms."
                        ),
                        "severity": "medium",
                        "source_document_ids": [invoice["document_id"], contract["document_id"]],
                    })
    return findings


def check_not_to_exceed_not_breached(by_contract: dict) -> list[dict]:
    findings = []
    for contract_no, group in by_contract.items():
        contract = group["contract"]
        if not contract:
            continue
        vendor = contract["facts"].get("vendor")
        vendor_name = vendor[0][0]["value"] if vendor else contract_no

        nte_facts = contract["facts"].get("not_to_exceed_amount")
        current_nte = nte_facts[0][0]["value"] if nte_facts else None
        for amendment in group["amendments"]:
            revised = amendment["facts"].get("revised_not_to_exceed_amount")
            sig = amendment["facts"].get("signature_status")
            if revised and sig and sig[0][0].get("vendor_signed") and sig[0][0].get("gc_signed"):
                current_nte = revised[0][0]["to"]

        total_invoiced = 0.0
        for invoice in group["invoices"]:
            total_facts = invoice["facts"].get("total_due")
            if total_facts:
                total_invoiced += total_facts[0][0]["value"]

        if current_nte is not None and total_invoiced > current_nte:
            findings.append({
                "title": f"Not-to-exceed amount breached: {vendor_name}",
                "description": (
                    f"Total invoiced (${total_invoiced:,.2f}) exceeds the current "
                    f"not-to-exceed amount (${current_nte:,.2f}) for contract {contract_no}."
                ),
                "severity": "high",
                "source_document_ids": [contract["document_id"]] + [i["document_id"] for i in group["invoices"]],
            })
    return findings


CHECKERS = {
    "insurance_certificate_on_file": check_insurance_certificate_on_file,
    "change_order_signed_before_invoicing": check_change_order_signed_before_invoicing,
    "invoice_matches_current_terms": check_invoice_matches_current_terms,
    "not_to_exceed_not_breached": check_not_to_exceed_not_breached,
}


def run_rule(conn, pile_id: str, rule_text: str) -> dict:
    """
    Classify a single rule and run whichever checkers it matches.
    Returns {"matched_checkers": [...], "findings": [...]} or, if the
    rule matches nothing, {"matched_checkers": [], "unsupported": True}
    - an honest signal rather than a silently empty result that looks
    identical to "checked, found nothing."
    """

    matched = classify_rule(rule_text)
    if not matched:
        return {"matched_checkers": [], "unsupported": True, "findings": []}

    by_contract = load_facts_by_contract(conn, pile_id)
    findings = []
    for checker_name in matched:
        findings.extend(CHECKERS[checker_name](by_contract))

    return {"matched_checkers": matched, "unsupported": False, "findings": findings}
