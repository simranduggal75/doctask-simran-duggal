"""
Movement 2: examine the pile against user-supplied rules.

Each active rule becomes its own stage (check_rule_<id>), generated
from the rules actually loaded for this pile at run-start time - not a
fixed script. This means the state machine's ordinary resumability
applies per rule: if checking rule 3 of 5 crashes, a resumed run skips
1-2 (already committed) and only redoes 3 onward.
"""

from __future__ import annotations

import json

from app.rule_engine import run_rule
from app.state_machine import new_id, now_iso


def load_active_rules(conn, pile_id: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT id, rule_text FROM rules WHERE pile_id = ? AND active = 1 ORDER BY created_at",
        (pile_id,),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def make_check_rule_stage(conn, pile_id: str, rule_id: str, rule_text: str):
    def check(context: dict) -> dict:
        result = run_rule(conn, pile_id, rule_text)
        return {"decision": "proceed", "rule_id": rule_id, "rule_text": rule_text, **result}

    return check


def make_compile_findings_stage(conn, pile_id: str, run_id: str, rule_ids: list[str]):
    def compile_findings(context: dict) -> dict:
        created_findings = []
        unsupported_rules = []

        for rule_id in rule_ids:
            stage_name = f"check_rule_{rule_id}"
            result = context["results"].get(stage_name, {})

            if result.get("unsupported"):
                unsupported_rules.append({"rule_id": rule_id, "rule_text": result.get("rule_text")})
                continue

            for finding in result.get("findings", []):
                finding_id = new_id()
                conn.execute(
                    "INSERT INTO findings (id, pile_id, rule_id, title, description, severity, "
                    "source_document_ids, status, created_in_run_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        finding_id, pile_id, rule_id, finding["title"], finding["description"],
                        finding["severity"], json.dumps(finding["source_document_ids"]),
                        run_id, now_iso(),
                    ),
                )
                created_findings.append({**finding, "id": finding_id, "rule_id": rule_id})

        conn.execute(
            "INSERT INTO change_log (id, pile_id, event_type, description, run_id, created_at) "
            "VALUES (?, ?, 'findings_created', ?, ?, ?)",
            (
                new_id(), pile_id,
                f"{len(created_findings)} finding(s) created from {len(rule_ids)} rule(s), "
                f"{len(unsupported_rules)} rule(s) unsupported.",
                run_id, now_iso(),
            ),
        )

        return {
            "decision": "proceed",
            "findings_created": len(created_findings),
            "findings": created_findings,
            "unsupported_rules": unsupported_rules,
        }

    return compile_findings


def build_examine_plan(conn, pile_id: str, run_id: str):
    """
    Returns (stage_names, stages) for an examine run against this
    pile's currently active rules - built dynamically per call, since
    the rule set is user data, not a fixed pipeline.
    """

    rules = load_active_rules(conn, pile_id)
    rule_ids = [rule_id for rule_id, _ in rules]

    stage_names = [f"check_rule_{rule_id}" for rule_id in rule_ids] + ["compile_findings"]
    stages = [
        (f"check_rule_{rule_id}", make_check_rule_stage(conn, pile_id, rule_id, rule_text))
        for rule_id, rule_text in rules
    ]
    stages.append(("compile_findings", make_compile_findings_stage(conn, pile_id, run_id, rule_ids)))

    return stage_names, stages
