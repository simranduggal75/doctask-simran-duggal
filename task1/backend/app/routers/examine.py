import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH
from app.db import connect
from app.movement_examine import build_examine_plan, load_active_rules
from app.state_machine import ActiveRunConflictError, get_run_status, new_id, now_iso, run_stages, start_run

router = APIRouter(prefix="/api/v1", tags=["examine"])


class AddRuleRequest(BaseModel):
    rule_text: str


@router.post("/piles/{pile_id}/rules")
def add_rule(pile_id: str, request: AddRuleRequest):
    conn = connect(DB_PATH)
    pile = conn.execute("SELECT id FROM piles WHERE id = ?", (pile_id,)).fetchone()
    if pile is None:
        raise HTTPException(status_code=404, detail=f"Unknown pile: {pile_id}")

    rule_id = new_id()
    conn.execute(
        "INSERT INTO rules (id, pile_id, rule_text, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (rule_id, pile_id, request.rule_text, now_iso()),
    )
    return {"rule_id": rule_id, "pile_id": pile_id, "rule_text": request.rule_text, "active": True}


@router.get("/piles/{pile_id}/rules")
def list_rules(pile_id: str):
    conn = connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, rule_text, active, created_at FROM rules WHERE pile_id = ? ORDER BY created_at",
        (pile_id,),
    ).fetchall()
    return [
        {"rule_id": r[0], "rule_text": r[1], "active": bool(r[2]), "created_at": r[3]}
        for r in rows
    ]


@router.post("/piles/{pile_id}/examine")
def run_examine(pile_id: str):
    conn = connect(DB_PATH)
    pile = conn.execute("SELECT id FROM piles WHERE id = ?", (pile_id,)).fetchone()
    if pile is None:
        raise HTTPException(status_code=404, detail=f"Unknown pile: {pile_id}")

    rule_count = conn.execute(
        "SELECT COUNT(*) FROM rules WHERE pile_id = ? AND active = 1", (pile_id,)
    ).fetchone()[0]
    if rule_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No active rules for this pile - add at least one rule before examining.",
        )

    # Stage names depend only on which rules exist (not on the run_id
    # itself), so they can be computed before start_run allocates one.
    rule_ids = [rule_id for rule_id, _ in load_active_rules(conn, pile_id)]
    stage_names = [f"check_rule_{rid}" for rid in rule_ids] + ["compile_findings"]

    try:
        run_id = start_run(
            conn, pile_id=pile_id, run_type="examine", stage_names=stage_names,
        )
    except ActiveRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _, stages = build_examine_plan(conn, pile_id, run_id)

    try:
        run_stages(conn, run_id, stages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Examine run failed: {exc}") from exc

    return get_run_status(conn, run_id)


@router.get("/piles/{pile_id}/findings")
def list_findings(pile_id: str, status: str | None = None):
    conn = connect(DB_PATH)
    query = "SELECT id, rule_id, title, description, severity, source_document_ids, status, created_at FROM findings WHERE pile_id = ?"
    params: list = [pile_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at"

    rows = conn.execute(query, params).fetchall()
    return [
        {
            "finding_id": r[0],
            "rule_id": r[1],
            "title": r[2],
            "description": r[3],
            "severity": r[4],
            "source_document_ids": json.loads(r[5]),
            "status": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]
