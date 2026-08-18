from fastapi import FastAPI

from app.db import connect
from app.config import DB_PATH
from app.routers.approvals import router as approvals_router
from app.routers.examine import router as examine_router
from app.routers.piles import router as piles_router
from app.routers.watch import router as watch_router

app = FastAPI(
    title="Agentic Vendor Compliance Register",
    version="0.1.0",
)

app.include_router(piles_router)
app.include_router(examine_router)
app.include_router(watch_router)
app.include_router(approvals_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "agentic-vendor-compliance-register"}


@app.on_event("startup")
def ensure_schema():
    connect(DB_PATH).close()
