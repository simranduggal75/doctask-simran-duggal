from fastapi import FastAPI

app = FastAPI(
    title="Daily Field Report Pipeline",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "daily-field-report-pipeline",
    }