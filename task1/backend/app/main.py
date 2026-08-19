from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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


class NoCacheStaticFiles(StaticFiles):
    """
    Plain StaticFiles lets the browser's own HTTP cache decide whether
    to reuse a previously-fetched app.js/style.css, independent of
    whether the file changed on disk - confirmed directly during
    testing: a fresh no-store fetch always saw the current file, but a
    normal page load kept executing a stale cached script. Real users
    hitting this after any future update would see the same silent
    staleness. This is a review tool people are meant to trust; a
    five-line fix is worth it.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serves the review UI as static files. Mounted last so it only
# catches paths the API routers above don't already claim.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount(
        "/",
        NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )
