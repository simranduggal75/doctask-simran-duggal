"""
Deterministic, pattern-based document classification and fact
extraction. This is the default, no-API-key path - grounded by
construction, since it only ever extracts a fact when it can point to
the literal text it came from. Never invents a field it can't find.

Not a claim that regex parsing generalizes to arbitrary real-world
paperwork - it works here because the corpus documents follow a
consistent "Label: Value" convention, which is realistic for a single
GC's own contract templates but not for arbitrary third-party
documents. See llm_client.py for the swappable path that replaces this
with real model reasoning once an API key is available - same output
shape, so nothing downstream needs to change.
"""

from __future__ import annotations

import re

INJECTION_PATTERNS = [
    r"ignore (?:all )?(?:previous|prior) instructions",
    r"system note to (?:reviewing )?agent",
    r"mark (?:this|the) .* (?:as )?(?:fully )?compliant",
    r"pre-?approved by .*(?:compliance|review)",
    r"(?:already |has )?been pre-?approved",
    r"do not flag this",
    r"skip (?:further )?review",
    r"approve all pending",
    r"approve (?:this|the) .* automatically",
    r"exempt from the .*(?:requirement|certificate|compliance)",
    r"do not (?:raise|create) (?:a |any )?finding",
    r"no further review (?:is )?needed",
]


def detect_injection_attempts(text: str) -> list[str]:
    """
    Scan for text that reads as an instruction directed at the system
    reviewing the document, rather than content describing the document
    itself. Returns each flagged paragraph verbatim, as one coherent
    block rather than fragmented sentence-by-sentence - reported as
    data about the document, never acted on. This is the mechanism
    behind behavior 8 ("does not take orders from its documents").
    """

    found = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, paragraph, flags=re.IGNORECASE):
                found.append(paragraph)
                break
    return found


def classify_document(text: str) -> str:
    upper = text.upper()
    if "SUBCONTRACTOR AGREEMENT" in upper:
        return "contract"
    if "CHANGE ORDER" in upper and "AMENDMENT" in upper:
        return "amendment"
    if "CERTIFICATE OF LIABILITY INSURANCE" in upper:
        return "certificate"
    if "INVOICE" in upper:
        return "invoice"
    return "unknown"


def _label_value(text: str, label: str) -> tuple[str, str] | None:
    """Find a 'Label: value' line, return (value, the matched line) or None."""

    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip(), match.group(0).strip()


def _money(text: str) -> float | None:
    match = re.search(r"\$?([\d,]+\.\d{2})", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def extract_contract_facts(text: str) -> list[dict]:
    facts = []

    for label, fact_type in [
        ("Contract No", "contract_no"),
        ("Vendor", "vendor"),
        ("Project", "project"),
        ("Effective Date", "effective_date"),
    ]:
        found = _label_value(text, label)
        if found:
            value, excerpt = found
            facts.append({"fact_type": fact_type, "fact_json": {"value": value}, "source_excerpt": excerpt})

    nte_match = re.search(r"Not-to-exceed amount:\s*(\$[\d,]+\.\d{2})", text)
    if nte_match:
        facts.append({
            "fact_type": "not_to_exceed_amount",
            "fact_json": {"value": _money(nte_match.group(1))},
            "source_excerpt": nte_match.group(0),
        })

    rate_match = re.search(
        r"Labor rate for work outside the defined scope:\s*(\$[\d,]+\.\d{2}) per hour", text
    )
    if rate_match:
        facts.append({
            "fact_type": "out_of_scope_hourly_rate",
            "fact_json": {"value": _money(rate_match.group(1))},
            "source_excerpt": rate_match.group(0),
        })

    payment_match = re.search(r"Net (\d+) days", text)
    if payment_match:
        facts.append({
            "fact_type": "payment_terms_days",
            "fact_json": {"value": int(payment_match.group(1))},
            "source_excerpt": payment_match.group(0),
        })

    if re.search(r"shall provide a current Certificate of Insurance", text, flags=re.IGNORECASE):
        facts.append({
            "fact_type": "insurance_certificate_required",
            "fact_json": {"value": True},
            "source_excerpt": "Certificate of Insurance requirement found in INSURANCE clause",
        })

    if re.search(r"requires? a written change order signed by both parties", text, flags=re.IGNORECASE):
        facts.append({
            "fact_type": "change_order_requires_signature",
            "fact_json": {"value": True},
            "source_excerpt": "Change Orders clause requires signature by both parties",
        })

    return facts


def extract_amendment_facts(text: str) -> list[dict]:
    facts = []

    for label, fact_type in [
        ("Amendment No", "amendment_no"),
        ("Contract No", "contract_no"),
        ("Vendor", "vendor"),
        ("Date", "amendment_date"),
    ]:
        found = _label_value(text, label)
        if found:
            value, excerpt = found
            facts.append({"fact_type": fact_type, "fact_json": {"value": value}, "source_excerpt": excerpt})

    nte_change = re.search(
        r"not-to-exceed amount increased from (\$[\d,]+\.\d{2}) to (\$[\d,]+\.\d{2})", text
    )
    if nte_change:
        facts.append({
            "fact_type": "revised_not_to_exceed_amount",
            "fact_json": {"from": _money(nte_change.group(1)), "to": _money(nte_change.group(2))},
            "source_excerpt": nte_change.group(0),
        })

    rate_change = re.search(
        r"rate for (?:additional/out-of-scope|out-of-scope) work revised from "
        r"(\$[\d,]+\.\d{2})/hr to (\$[\d,]+\.\d{2})/hr,\s*effective for all work "
        r"performed on or after (\d{4}-\d{2}-\d{2})",
        text,
    )
    if rate_change:
        facts.append({
            "fact_type": "revised_hourly_rate",
            "fact_json": {
                "from": _money(rate_change.group(1)),
                "to": _money(rate_change.group(2)),
                "effective_date": rate_change.group(3),
            },
            "source_excerpt": rate_change.group(0),
        })

    # Signature status: look at the two SIGNATURES lines specifically.
    gc_signed = bool(re.search(r"Meridian Builders GC:\s*/s/", text))
    vendor_signed = bool(
        re.search(r"^(?!Meridian Builders GC:).*:\s*/s/", text, flags=re.MULTILINE)
        and not re.search(r"PENDING SIGNATURE", text, flags=re.IGNORECASE)
    )
    facts.append({
        "fact_type": "signature_status",
        "fact_json": {"gc_signed": gc_signed, "vendor_signed": vendor_signed},
        "source_excerpt": _extract_signature_block(text),
    })

    return facts


def _extract_signature_block(text: str) -> str:
    match = re.search(r"SIGNATURES\n(.+?)(?:\n\n|\Z)", text, flags=re.DOTALL)
    return match.group(0).strip() if match else ""


def extract_invoice_facts(text: str) -> list[dict]:
    facts = []

    for label, fact_type in [
        ("Invoice No", "invoice_no"),
        ("Date", "invoice_date"),
        ("Contract No", "contract_no"),
    ]:
        found = _label_value(text, label)
        if found:
            value, excerpt = found
            facts.append({"fact_type": fact_type, "fact_json": {"value": value}, "source_excerpt": excerpt})

    total_match = re.search(r"TOTAL DUE:\s*(\$[\d,]+\.\d{2})", text)
    if total_match:
        facts.append({
            "fact_type": "total_due",
            "fact_json": {"value": _money(total_match.group(1))},
            "source_excerpt": total_match.group(0),
        })

    # Line items: description ... $amount, optionally with "N hrs @ $rate/hr".
    line_items_block = re.search(r"LINE ITEMS\n(.+?)\n\nTOTAL DUE", text, flags=re.DOTALL)
    if line_items_block:
        for line in _split_line_items(line_items_block.group(1)):
            amount_match = re.search(r"\$([\d,]+\.\d{2})\s*$", line)
            if not amount_match:
                continue
            item = {"description": line.strip(), "amount": float(amount_match.group(1).replace(",", ""))}

            rate_match = re.search(r"(\d+(?:\.\d+)?)\s*hrs?\s*@\s*\$(\d+(?:\.\d+)?)\s*/\s*hr", line)
            if rate_match:
                item["hours"] = float(rate_match.group(1))
                item["rate_used"] = float(rate_match.group(2))

            if re.search(r"change order", line, flags=re.IGNORECASE):
                item["references_change_order"] = True

            facts.append({
                "fact_type": "invoice_line_item",
                "fact_json": item,
                "source_excerpt": line.strip(),
            })

    for note in detect_injection_attempts(text):
        facts.append({
            "fact_type": "embedded_instruction_to_system",
            "fact_json": {"text": note},
            "source_excerpt": note,
        })

    return facts


def _split_line_items(block: str) -> list[str]:
    """
    A line item may wrap across more than one physical line before its
    trailing dollar amount appears - sometimes the continuation line is
    bare (just the amount, as in the 40hr Ridgeline line item),
    sometimes it carries more text before the amount (a long
    description, or an injection attempt padded onto the line). Either
    way, accumulate lines until one actually ends in a dollar amount -
    that's what closes one logical line item - rather than only
    special-casing the bare-amount case and silently losing whatever
    text came before a continuation line that wasn't bare.
    """

    raw_lines = [l for l in block.split("\n") if l.strip()]
    merged: list[str] = []
    buffer = ""

    for line in raw_lines:
        buffer = f"{buffer} {line.strip()}".strip() if buffer else line.strip()
        if re.search(r"\$[\d,]+\.\d{2}\s*$", buffer):
            merged.append(buffer)
            buffer = ""

    if buffer:
        # No trailing amount ever appeared - keep it rather than
        # silently dropping it, even though extract_invoice_facts will
        # skip it for lacking an amount to record as a fact.
        merged.append(buffer)

    return merged


def extract_certificate_facts(text: str) -> list[dict]:
    facts = []

    for label, fact_type in [
        ("Insured", "insured_vendor"),
        ("Policy No", "policy_no"),
    ]:
        found = _label_value(text, label)
        if found:
            value, excerpt = found
            facts.append({"fact_type": fact_type, "fact_json": {"value": value}, "source_excerpt": excerpt})

    contract_match = re.search(r"Contract No:\s*([\w-]+)", text)
    if contract_match:
        facts.append({
            "fact_type": "contract_no",
            "fact_json": {"value": contract_match.group(1)},
            "source_excerpt": contract_match.group(0),
        })

    dates_match = re.search(r"Effective:\s*(\d{4}-\d{2}-\d{2})\s*Expires:\s*(\d{4}-\d{2}-\d{2})", text)
    if dates_match:
        facts.append({
            "fact_type": "coverage_period",
            "fact_json": {"effective": dates_match.group(1), "expires": dates_match.group(2)},
            "source_excerpt": dates_match.group(0),
        })

    return facts


EXTRACTORS = {
    "contract": extract_contract_facts,
    "amendment": extract_amendment_facts,
    "invoice": extract_invoice_facts,
    "certificate": extract_certificate_facts,
}


def extract_facts(doc_type: str, text: str) -> list[dict]:
    extractor = EXTRACTORS.get(doc_type)
    if extractor is None:
        return []
    return extractor(text)
