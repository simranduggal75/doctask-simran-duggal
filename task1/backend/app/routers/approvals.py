"""
The human gate: approve/reject deliverable versions and findings.

Findings review is deliberately batch + item-by-item in one call,
matching the task's own wording: "approves what is right and rejects
what is wrong in the same review, item by item, and the system
respects every decision. Rejecting one finding does not discard the
rest." Every decision in a batch is applied independently - one item
failing (unknown id, wrong pile, already decided) never blocks or
rolls back the others.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH
from app.db import connect
from app.state_machine import new_id, now_iso

router = APIRouter(prefix="/api/v1", tags=["approvals"])


class DecisionRequest(BaseModel):
    reason: str | None = None
    decided_by: str = "human"


@router.post("/piles/{pile_id}/register/{version_number}/approve")
def approve_register_version(pile_id: str, version_number: int, request: DecisionRequest = DecisionRequest()):
    return _decide_version(pile_id, version_number, "approved", request)


@router.post("/piles/{pile_id}/register/{version_number}/reject")
def reject_register_version(pile_id: str, version_number: int, request: DecisionRequest = DecisionRequest()):
    return _decide_version(pile_id, version_number, "rejected", request)


def _decide_version(pile_id: str, version_number: int, new_status: str, request: DecisionRequest):
    conn = connect(DB_PATH)
    row = conn.execute(
        "SELECT id, status FROM deliverable_versions WHERE pile_id = ? AND version_number = ?",
        (pile_id, version_number),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No register version {version_number} for pile {pile_id}",
        )

    version_id, current_status = row
    if current_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Register version {version_number} was already decided "
                f"(status={current_status!r}) - not silently re-decided."
            ),
        )

    conn.execute(
        "UPDATE deliverable_versions SET status = ? WHERE id = ?", (new_status, version_id)
    )
    conn.execute(
        "INSERT INTO change_log (id, pile_id, event_type, description, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            new_id(), pile_id, f"version_{new_status}",
            f"Register version {version_number} {new_status} by {request.decided_by}"
            + (f": {request.reason}" if request.reason else ""),
            now_iso(),
        ),
    )

    return {
        "pile_id": pile_id, "version_number": version_number,
        "version_id": version_id, "status": new_status,
    }


class FindingDecision(BaseModel):
    finding_id: str
    decision: Literal["approve", "reject"]
    reason: str | None = None


class ReviewFindingsRequest(BaseModel):
    decisions: list[FindingDecision]
    decided_by: str = "human"


@router.post("/piles/{pile_id}/findings/review")
def review_findings(pile_id: str, request: ReviewFindingsRequest):
    conn = connect(DB_PATH)
    results = []

    for item in request.decisions:
        row = conn.execute(
            "SELECT pile_id, status FROM findings WHERE id = ?", (item.finding_id,)
        ).fetchone()

        if row is None:
            results.append({
                "finding_id": item.finding_id, "applied": False,
                "error": "unknown finding_id",
            })
            continue

        finding_pile_id, current_status = row
        if finding_pile_id != pile_id:
            results.append({
                "finding_id": item.finding_id, "applied": False,
                "error": f"finding belongs to pile {finding_pile_id}, not {pile_id}",
            })
            continue

        if current_status != "pending":
            # Explicit, not silent: report it rather than quietly
            # overwriting or quietly no-op'ing an existing decision.
            results.append({
                "finding_id": item.finding_id, "applied": False,
                "error": f"already decided (status={current_status!r})",
                "existing_status": current_status,
            })
            continue

        new_status = "approved" if item.decision == "approve" else "rejected"
        conn.execute(
            "UPDATE findings SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (new_status, now_iso(), request.decided_by, item.finding_id),
        )
        conn.execute(
            "INSERT INTO change_log (id, pile_id, event_type, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                new_id(), pile_id, f"finding_{new_status}",
                f"Finding {item.finding_id} {new_status} by {request.decided_by}"
                + (f": {item.reason}" if item.reason else ""),
                now_iso(),
            ),
        )
        results.append({"finding_id": item.finding_id, "applied": True, "status": new_status})

    return {
        "pile_id": pile_id,
        "decisions_submitted": len(request.decisions),
        "decisions_applied": sum(1 for r in results if r["applied"]),
        "results": results,
    }
