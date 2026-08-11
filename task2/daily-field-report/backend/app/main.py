from fastapi import FastAPI

from app.routers.reports import router as reports_router

app = FastAPI(
    title="Daily Field Report Pipeline",
    version="0.1.0",
)

app.include_router(reports_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "daily-field-report-pipeline",
    }