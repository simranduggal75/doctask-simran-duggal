from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.report_intake import create_report

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
        return await create_report(
            notes=notes,
            photos=photos,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to create field report.",
        ) from exc