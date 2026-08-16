from fastapi import FastAPI

from app.routers.batch import router as batch_router
from app.routers.reports import router as reports_router
from app.routers.rollups import router as rollups_router

app = FastAPI(
    title="Daily Field Report Pipeline",
    version="0.1.0",
)

app.include_router(reports_router)
app.include_router(batch_router)
app.include_router(rollups_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "daily-field-report-pipeline",
    }