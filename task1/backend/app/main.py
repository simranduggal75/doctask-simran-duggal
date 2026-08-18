from fastapi import FastAPI

from app.db import connect
from app.config import DB_PATH
from app.routers.piles import router as piles_router

app = FastAPI(
    title="Agentic Vendor Compliance Register",
    version="0.1.0",
)

app.include_router(piles_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "agentic-vendor-compliance-register"}


@app.on_event("startup")
def ensure_schema():
    connect(DB_PATH).close()
