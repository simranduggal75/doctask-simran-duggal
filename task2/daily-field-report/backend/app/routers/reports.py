from typing import Annotated
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.report_intake import create_report
from app.services.report_pipeline import (
    ReportPipelineError,
    process_report,
)

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
)


@router.post("")
async def create_field_report(
    notes: Annotated[str | None, Form()] = None,
    photos: Annotated[
        list[UploadFile],
        File(description="Site photos"),
    ] = [],
):
    try:
        # ---------------------------------------------------------
        # Step 1: Persist incoming notes and photos
        # ---------------------------------------------------------

        intake = await create_report(
            notes=notes,
            photos=photos,
        )

        # ---------------------------------------------------------
        # Step 2: Run daily report generation
        # ---------------------------------------------------------

        pipeline_result = await process_report(
            report_id=intake["report_id"],
            report_dir=Path(intake["report_dir"]),
            notes=notes,
            photo_filenames=intake["photos"],
        )

        # ---------------------------------------------------------
        # Step 3: Return combined result
        # ---------------------------------------------------------

        return {
    "report_id": intake["report_id"],
    "status": pipeline_result["status"],
    "intake": {
        "has_notes": intake["has_notes"],
        "photo_count": intake["photo_count"],
        "photos": intake["photos"],
    },
    "report": pipeline_result["report_data"],
    "photo_captions": pipeline_result["photo_captions"],
    "photo_analysis": pipeline_result["photo_analysis"],
    "html_path": pipeline_result["html_path"],
}

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except ReportPipelineError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to create field report.",
        ) from exc