"""Recipe Planner — Phase 8 entry point."""

import logging
import logging.handlers
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import Base, engine, run_migrations, tune_sqlite
from app.routers import households, pantry, preferences, recipes, meal_plan
from app.routers import config as config_router

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FMT  = "%(asctime)s %(levelname)-8s %(name)-35s %(message)s"
DATE_FMT = "%H:%M:%S"

_stream = logging.StreamHandler()
_stream.setLevel(logging.DEBUG)
_stream.setFormatter(logging.Formatter(LOG_FMT, datefmt=DATE_FMT))


class _RingHandler(logging.Handler):
    def __init__(self, maxlen=1_000):
        super().__init__()
        self._buf: deque = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter(LOG_FMT, datefmt=DATE_FMT))

    def emit(self, record):
        try:
            self._buf.append({
                "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level":   record.levelname,
                "logger":  record.name,
                "message": self.format(record),
            })
        except Exception:
            self.handleError(record)

    def records(self):
        return list(self._buf)


_ring = _RingHandler(1_000)
_ring.setLevel(logging.DEBUG)

root = logging.getLogger()
root.setLevel(logging.DEBUG)
root.addHandler(_stream)
root.addHandler(_ring)

for _lib in ("urllib3", "httpx", "httpcore", "requests", "bs4", "lxml"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Recipe Planner",
    description="Pantry-first household dinner meal planner.",
    version="0.8.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

run_migrations()
Base.metadata.create_all(bind=engine)
tune_sqlite()
log.info("Recipe Planner v0.8.0 started")

# ── Background scrape scheduler (Patch 12) ────────────────────────────────────
from app import scrape_job


@app.on_event("startup")
def _start_scrape_job():
    scrape_job.start()


@app.on_event("shutdown")
def _stop_scrape_job():
    scrape_job.stop()

app.include_router(households.router,    prefix="/api")
app.include_router(pantry.router,        prefix="/api")
app.include_router(preferences.router,   prefix="/api")
app.include_router(recipes.router,       prefix="/api")
app.include_router(meal_plan.router,     prefix="/api")
app.include_router(config_router.router, prefix="/api")

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.8.0"}


@app.get("/api/logs", tags=["debug"])
def get_logs(
    level: str = Query(default="DEBUG"),
    last_n: int = Query(default=200, ge=1, le=1_000),
    logger_filter: str = Query(default=""),
):
    min_level = _LEVEL_ORDER.get(level.upper(), 10)
    lf = logger_filter.lower()
    filtered = [
        r for r in _ring.records()
        if _LEVEL_ORDER.get(r["level"], 0) >= min_level
        and (not lf or lf in r["logger"].lower() or lf in r["message"].lower())
    ]
    return {"total_buffered": len(_ring.records()), "returned": len(filtered[-last_n:]),
            "filters": {"min_level": level.upper(), "logger_filter": logger_filter},
            "logs": filtered[-last_n:]}


@app.get("/api/workflow")
def workflow():
    return {"steps": [
        "GET  /api/meal-plan/pantry-review/{household_id}",
        "POST /api/meal-plan/week-intent/{household_id}/{week_start_date}",
        "GET  /api/meal-plan/suggest",
        "POST /api/recipes/{id}/reject (optional)",
        "POST /api/meal-plan/selections",
        "GET  /api/meal-plan/shopping-list",
        "POST /api/meal-plan/entries/{id}/review",
    ]}


STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        return FileResponse(str(STATIC_DIR / "index.html"))
