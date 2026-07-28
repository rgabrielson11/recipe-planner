"""
Nightly background scrape job — Phase 10 Patch 12
===================================================

Runs collect_and_scrape() once per day at a configurable hour (server local
time) so the recipe cache is always warm and suggest runs never wait on the
network.  Configuration lives in recipe_sources.yaml under `discovery:`:

    background_scrape_enabled: true
    background_scrape_hour: 3        # 0-23, server local time
    background_max_scraped: 60       # scrape budget per nightly run

The YAML is re-read every loop iteration, so edits take effect without a
restart.  A shared lock in recipe_discovery guarantees the job never runs
concurrently with a cold-cache suggest scrape.

Status of the last run is persisted to scrape_status.json next to the SQLite
DB and exposed via GET /api/config/scrape-status.
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app import config_files, recipe_discovery
from app.database import SessionLocal

log = logging.getLogger(__name__)

_STATUS_PATH = Path(os.getenv("DATABASE_PATH", "/app/data/recipe_planner.db")).parent / "scrape_status.json"

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_POLL_SECONDS = 300   # re-check config / next-run time every 5 minutes


def get_status() -> dict:
    """Last-run stats for the UI. Empty dict if the job has never run."""
    try:
        return json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(status: dict) -> None:
    try:
        _STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("scrape_job: could not write status file: %s", e)


def run_scrape_once(trigger: str = "scheduled") -> dict:
    """
    Run one background scrape.  Non-blocking on the shared lock — if a
    scrape is already in progress (e.g. a cold-cache suggest run), this
    records a skip instead of queueing behind it.
    """
    log.info("scrape_job: starting background scrape (trigger=%s)", trigger)
    cfg    = config_files.get_discovery_config()
    budget = int(cfg.get("background_max_scraped", 60))

    db = SessionLocal()
    try:
        stats = recipe_discovery.collect_and_scrape(
            db, budget=budget, progress_household=None, wait_for_lock=False,
        )
    except Exception as e:
        log.error("scrape_job: background scrape failed: %s", e)
        stats = {"error": str(e)}
    finally:
        db.close()

    status = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "trigger":  trigger,
        **stats,
    }
    _write_status(status)
    return status


def _next_run(now: datetime, hour: int) -> datetime:
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _loop() -> None:
    log.info("scrape_job: scheduler thread started")
    while not _stop_event.is_set():
        try:
            cfg = config_files.get_discovery_config()
            if not bool(cfg.get("background_scrape_enabled", True)):
                if _stop_event.wait(timeout=_POLL_SECONDS):
                    break
                continue

            hour    = int(cfg.get("background_scrape_hour", 3)) % 24
            now     = datetime.now()
            target  = _next_run(now, hour)
            wait_s  = (target - now).total_seconds()

            # Sleep in short slices so config edits / shutdown are honored
            if wait_s > _POLL_SECONDS:
                if _stop_event.wait(timeout=_POLL_SECONDS):
                    break
                continue

            if _stop_event.wait(timeout=wait_s):
                break

            # Re-check enabled flag right before firing
            cfg = config_files.get_discovery_config()
            if bool(cfg.get("background_scrape_enabled", True)):
                run_scrape_once(trigger="scheduled")
                # Guard against double-fire within the same minute
                if _stop_event.wait(timeout=90):
                    break
        except Exception as e:
            log.error("scrape_job: scheduler loop error: %s", e)
            if _stop_event.wait(timeout=_POLL_SECONDS):
                break
    log.info("scrape_job: scheduler thread stopped")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="scrape-job", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()
