import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH
from app.db import connect
from app.llm_client import get_client
from app.movement_understand import (
    make_build_register_stage,
    make_detect_disagreements_stage,
    make_extract_facts_stage,
    make_ingest_stage,
)
from app.state_machine import (
    ActiveRunConflictError,
    get_run_status,
    new_id,
    now_iso,
    run_stages,
    start_run,
)

router = APIRouter(prefix="/api/v1", tags=["piles"])


class CreatePileRequest(BaseModel):
    name: str
    source_dir: str


@router.post("/piles")
def create_pile(request: CreatePileRequest):
    conn = connect(DB_PATH)
    pile_id = new_id()
    conn.execute(
        "INSERT INTO piles (id, name, watch_dir, created_at) VALUES (?, ?, ?, ?)",
        (pile_id, request.name, request.source_dir, now_iso()),
    )
    return {"pile_id": pile_id, "name": request.name, "watch_dir": request.source_dir}


@router.post("/piles/{pile_id}/understand")
def run_understand(pile_id: str):
    conn = connect(DB_PATH)
    pile = conn.execute("SELECT watch_dir FROM piles WHERE id = ?", (pile_id,)).fetchone()

    if pile is None:
        raise HTTPException(status_code=404, detail=f"Unknown pile: {pile_id}")

    from pathlib import Path

    source_dir = Path(pile[0])

    try:
        run_id = start_run(
            conn, pile_id=pile_id, run_type="understand",
            stage_names=["ingest", "extract_facts", "detect_disagreements", "build_register"],
        )
    except ActiveRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    client = get_client()
    stages = [
        ("ingest", make_ingest_stage(conn, pile_id, source_dir)),
        ("extract_facts", make_extract_facts_stage(conn, pile_id, run_id, client)),
        ("detect_disagreements", make_detect_disagreements_stage(conn, pile_id)),
        ("build_register", make_build_register_stage(conn, pile_id, run_id)),
    ]

    try:
        run_stages(conn, run_id, stages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Understand run failed: {exc}") from exc

    return get_run_status(conn, run_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    conn = connect(DB_PATH)
    try:
        return get_run_status(conn, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/piles/{pile_id}/register")
def get_latest_register(pile_id: str):
    conn = connect(DB_PATH)
    row = conn.execute(
        "SELECT id, version_number, content_json, content_hash, status, change_summary, created_at "
        "FROM deliverable_versions WHERE pile_id = ? ORDER BY version_number DESC LIMIT 1",
        (pile_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No register exists yet for pile {pile_id}")

    return {
        "version_id": row[0],
        "version_number": row[1],
        "content": json.loads(row[2]),
        "content_hash": row[3],
        "status": row[4],
        "change_summary": row[5],
        "created_at": row[6],
    }
