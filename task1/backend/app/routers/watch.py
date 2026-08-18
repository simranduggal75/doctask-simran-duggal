from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import DB_PATH
from app.db import connect
from app.llm_client import get_client
from app.movement_incremental import build_incremental_plan
from app.state_machine import ActiveRunConflictError, get_run_status, run_stages, start_run

router = APIRouter(prefix="/api/v1", tags=["watch"])


@router.post("/piles/{pile_id}/watch/scan")
def scan_for_new_documents(pile_id: str):
    """
    Polling-based watch: scans the pile's watch_dir for documents not
    yet ingested and produces a focused update for whatever is found.
    No persistent watcher process - call this periodically (e.g. from a
    scheduler) or on demand, same pattern as Task 2's nightly batch.
    """

    conn = connect(DB_PATH)
    pile = conn.execute("SELECT watch_dir FROM piles WHERE id = ?", (pile_id,)).fetchone()
    if pile is None:
        raise HTTPException(status_code=404, detail=f"Unknown pile: {pile_id}")

    source_dir = Path(pile[0])

    try:
        run_id = start_run(
            conn, pile_id=pile_id, run_type="incremental_update",
            stage_names=[
                "detect_new_documents", "ingest_new_documents", "extract_new_facts",
                "recompute_affected", "build_incremental_register",
            ],
        )
    except ActiveRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    client = get_client()
    _, stages = build_incremental_plan(conn, pile_id, run_id, source_dir, client)

    try:
        run_stages(conn, run_id, stages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Watch scan failed: {exc}") from exc

    return get_run_status(conn, run_id)


@router.get("/piles/{pile_id}/changes")
def get_change_log(pile_id: str):
    """What changed, when, and because of which source/run - the
    task's own required question, answered directly from change_log."""

    conn = connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, event_type, description, source_document_id, run_id, created_at "
        "FROM change_log WHERE pile_id = ? ORDER BY created_at",
        (pile_id,),
    ).fetchall()

    return [
        {
            "change_id": r[0], "event_type": r[1], "description": r[2],
            "source_document_id": r[3], "run_id": r[4], "created_at": r[5],
        }
        for r in rows
    ]
