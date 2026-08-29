"""
Config router — Phase 8
Exposes the YAML configuration files as editable API resources.
All writes use ruamel.yaml round-trip mode so comments are preserved.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from pydantic import BaseModel

from app import config_files, models

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["config"])


# ── Sources ───────────────────────────────────────────────────────────────────

class SourceIn(BaseModel):
    name:          str
    enabled:       bool          = True
    category_urls: list[str]     = []
    notes:         Optional[str] = None


@router.get("/sources")
def list_sources():
    """All sources from recipe_sources.yaml (enabled and disabled)."""
    try:
        return config_files.get_recipe_sources().get("sources", [])
    except Exception as e:
        log.error("Failed to load recipe_sources.yaml: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to load sources config: {e}")


@router.post("/sources", status_code=201)
def add_source(payload: SourceIn):
    """Add a new source (HTML category / directory page URLs)."""
    data    = config_files.load_yaml("recipe_sources.yaml")
    sources = data.setdefault("sources", [])
    if any(s.get("name") == payload.name for s in sources):
        raise HTTPException(400, f"Source '{payload.name}' already exists")
    entry: dict[str, Any] = {
        "name":          payload.name,
        "enabled":       payload.enabled,
        "category_urls": payload.category_urls,
    }
    if payload.notes:
        entry["notes"] = payload.notes
    sources.append(entry)
    config_files.save_yaml("recipe_sources.yaml", data)
    log.info("Config: added source '%s'", payload.name)
    return entry


@router.put("/sources/{name}")
def update_source(name: str, payload: SourceIn):
    """Update an existing source (enable/disable, edit URLs, rename)."""
    data    = config_files.load_yaml("recipe_sources.yaml")
    sources = data.get("sources", [])
    idx     = next((i for i, s in enumerate(sources) if s.get("name") == name), None)
    if idx is None:
        raise HTTPException(404, f"Source '{name}' not found")
    entry: dict[str, Any] = {
        "name":          payload.name,
        "enabled":       payload.enabled,
        "category_urls": payload.category_urls,
    }
    if payload.notes:
        entry["notes"] = payload.notes
    sources[idx] = entry
    config_files.save_yaml("recipe_sources.yaml", data)
    log.info("Config: updated source '%s'", name)
    return entry


@router.delete("/sources/{name}", status_code=204)
def delete_source(name: str):
    """Delete a source permanently."""
    data    = config_files.load_yaml("recipe_sources.yaml")
    sources = data.get("sources", [])
    before  = len(sources)
    data["sources"] = [s for s in sources if s.get("name") != name]
    if len(data["sources"]) == before:
        raise HTTPException(404, f"Source '{name}' not found")
    config_files.save_yaml("recipe_sources.yaml", data)
    log.info("Config: deleted source '%s'", name)


# ── Discovery settings ────────────────────────────────────────────────────────

class DiscoverySettingsIn(BaseModel):
    max_scraped_per_run:        int   = 60
    request_delay_seconds:      float = 1.5
    mealie_min_rating:          int   = 4
    mealie_favorites_count:     int   = 2
    min_scraped_rating:         float = 4.0
    min_scraped_reviews:        int   = 50
    stub_rescrape_days:         int   = 7
    background_scrape_enabled:  bool  = True
    background_scrape_hour:     int   = 3
    background_max_scraped:     int   = 60


@router.get("/discovery")
def get_discovery_settings():
    try:
        return config_files.get_discovery_config()
    except Exception as e:
        log.error("Failed to load discovery config: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to load discovery config: {e}")


@router.put("/discovery")
def update_discovery_settings(payload: DiscoverySettingsIn):
    data = config_files.load_yaml("recipe_sources.yaml")
    disc = data.setdefault("discovery", {})
    disc["max_scraped_per_run"]    = payload.max_scraped_per_run
    disc["request_delay_seconds"]  = payload.request_delay_seconds
    disc["mealie_min_rating"]      = payload.mealie_min_rating
    disc["mealie_favorites_count"] = payload.mealie_favorites_count
    disc["min_scraped_rating"]        = payload.min_scraped_rating
    disc["min_scraped_reviews"]       = payload.min_scraped_reviews
    disc["stub_rescrape_days"]        = payload.stub_rescrape_days
    disc["background_scrape_enabled"] = payload.background_scrape_enabled
    disc["background_scrape_hour"]    = payload.background_scrape_hour
    disc["background_max_scraped"]    = payload.background_max_scraped
    config_files.save_yaml("recipe_sources.yaml", data)
    log.info("Config: updated discovery settings")
    return dict(disc)


# ── Non-dinner keywords ───────────────────────────────────────────────────────

class KeywordsIn(BaseModel):
    keywords: list[str]


@router.get("/non-dinner-keywords")
def get_non_dinner_keywords():
    cfg = config_files.get_discovery_config()
    return {"keywords": list(cfg.get("non_dinner_title_keywords", []))}


@router.put("/non-dinner-keywords")
def update_non_dinner_keywords(payload: KeywordsIn):
    data = config_files.load_yaml("recipe_sources.yaml")
    disc = data.setdefault("discovery", {})
    disc["non_dinner_title_keywords"] = [k.strip() for k in payload.keywords if k.strip()]
    config_files.save_yaml("recipe_sources.yaml", data)
    log.info("Config: updated non-dinner keywords (%d items)", len(disc["non_dinner_title_keywords"]))
    return {"keywords": disc["non_dinner_title_keywords"]}


# ── Cooking vocabulary ────────────────────────────────────────────────────────

@router.get("/cooking-vocabulary")
def get_cooking_vocabulary():
    return config_files.get_cooking_vocabulary()


class CookingVocabIn(BaseModel):
    available_methods:  list[str] = []
    available_cookware: list[str] = []
    skill_levels:       list[str] = []


@router.get("/cooking-methods")
def get_cooking_methods():
    """Returns the known cooking method keys."""
    vocab = config_files.get_cooking_vocabulary()
    return {"methods": vocab.get("cooking_methods", []),
            "cookware": vocab.get("cookware", []),
            "skill_levels": vocab.get("skill_levels", [])}


# ── Mealie URL ────────────────────────────────────────────────────────────────

@router.get("/mealie-url")
def get_mealie_url():
    """
    Returns the configured Mealie base URL and group slug so the frontend
    can build deep links (e.g. manual import links for failed imports).
    Safe to expose — this is a LAN-only deployment with no auth layer.
    """
    import os
    base = os.getenv("MEALIE_BASE_URL", "").rstrip("/")
    return {
        "mealie_base_url": base,
        "group_slug":      "home",   # default for single-household Mealie installs
    }

# (RSS feed discovery endpoint removed in Phase 10 Patch 11 — sources are
# HTML category/directory pages only.)

# ── Background scrape job (Patch 12) ──────────────────────────────────────────

@router.get("/scrape-status")
def scrape_status():
    """Last background scrape run stats (from scrape_status.json)."""
    from app import scrape_job
    return scrape_job.get_status()


@router.post("/scrape-now", status_code=202)
def scrape_now():
    """
    Manually trigger a full background scrape (fire-and-forget).
    Poll /config/scrape-status for the result.
    """
    import threading
    from app import scrape_job
    threading.Thread(
        target=scrape_job.run_scrape_once,
        kwargs={"trigger": "manual"},
        daemon=True,
    ).start()
    return {"started": True}


@router.post("/sources/{name}/scrape", status_code=202)
def scrape_source(name: str, db: Session = Depends(get_db)):
    """
    Manually trigger a scrape restricted to a single named source.
    Runs in a background thread; poll /config/scrape-status for the result.
    """
    import threading
    from app import recipe_discovery

    # Verify source exists before firing the thread
    sources = config_files.get_recipe_sources().get("sources", [])
    match = next((s for s in sources if s.get("name") == name), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
    if not match.get("enabled", True):
        raise HTTPException(status_code=400, detail=f"Source '{name}' is disabled")

    def _run():
        from app.database import SessionLocal
        with SessionLocal() as _db:
            cfg = config_files.get_discovery_config()
            budget = int(cfg.get("background_max_scraped", 60))
            recipe_discovery.collect_and_scrape(
                _db,
                budget=budget,
                source_name=name,
                wait_for_lock=False,
            )

    threading.Thread(target=_run, daemon=True).start()
    log.info("Per-source manual scrape triggered for '%s'", name)
    return {"started": True, "source": name}


# ── Database maintenance ───────────────────────────────────────────────────────

@router.delete("/recipe-cache", status_code=200)
def wipe_recipe_cache(db: Session = Depends(get_db)):
    """
    Deletes all scraped recipe stubs and rejection history from the DB,
    leaving household preferences, pantry items, meal plan entries, and
    any Mealie-linked rows intact.

    Wipes:
      • recipes where mealie_slug IS NULL  (unconfirmed scrape stubs)
      • recipe_rejections                  (rejection history)

    Keeps:
      • recipes where mealie_slug IS NOT NULL  (confirmed + imported to Mealie)
      • households, preferences, pantry_items
      • weekly_intents, weekly_selections, meal_plan_entries
    """
    from sqlalchemy import text as _text

    # Count before so we can report what was deleted
    stub_count = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None)
    ).count()
    rejection_count = db.query(models.RecipeRejection).count()

    # Delete rejections first (FK constraint: recipe_rejections → recipes)
    db.query(models.RecipeRejection).delete()

    # Delete unconfirmed stubs only — keep Mealie-linked rows
    db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None)
    ).delete()

    db.commit()
    log.info(
        "Recipe cache wiped: %d stubs deleted, %d rejections deleted",
        stub_count, rejection_count,
    )
    return {
        "stubs_deleted":      stub_count,
        "rejections_deleted": rejection_count,
        "message": (
            f"Deleted {stub_count} recipe stub(s) and {rejection_count} "
            f"rejection record(s). Mealie-linked recipes are untouched."
        ),
    }


@router.get("/recipe-stats")
def get_recipe_stats(db: Session = Depends(get_db)):
    """
    Returns recipe stub counts grouped by source domain, plus totals for
    Mealie-linked recipes and rejection records.  Used by the Database page.
    """
    from urllib.parse import urlparse
    from collections import defaultdict

    stubs = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None),
        models.Recipe.source_url.isnot(None),
    ).all()

    by_source: dict[str, int] = defaultdict(int)
    for stub in stubs:
        url = stub.source_url or ""
        if url.startswith("mealie:"):
            host = "mealie (local)"
        else:
            try:
                host = urlparse(url).netloc.lstrip("www.") or "unknown"
            except Exception:
                host = "unknown"
        by_source[host] += 1

    mealie_linked = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.isnot(None)
    ).count()

    rejection_count = db.query(models.RecipeRejection).count()

    return {
        "stubs_by_source":  dict(sorted(by_source.items(), key=lambda x: -x[1])),
        "total_stubs":      len(stubs),
        "mealie_linked":    mealie_linked,
        "total_rejections": rejection_count,
    }


# ── Version ───────────────────────────────────────────────────────────────────

@router.get("/version")
def get_version():
    """Returns the app version string and git short hash."""
    import subprocess, os
    from pathlib import Path

    # Read semantic version from VERSION file (repo root or container root)
    ver = "unknown"
    for p in ["/app/VERSION", Path(__file__).parents[3] / "VERSION"]:
        try:
            ver = Path(p).read_text().strip()
            break
        except Exception:
            pass

    # Git short hash — available in git+compose deployments
    git_hash = ""
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode().strip()
    except Exception:
        pass

    from app import ollama_client as _oc
    return {
        "version":         ver,
        "git_hash":        git_hash,
        "ollama_url":      _oc.OLLAMA_BASE_URL or None,
        "ollama_model":    _oc.OLLAMA_MODEL if _oc.OLLAMA_BASE_URL else None,
        "ollama_available": _oc.is_available() if _oc.OLLAMA_BASE_URL else False,
    }



@router.get("/source-stub-counts")
def get_source_stub_counts(db: Session = Depends(get_db)):
    """Return stub count per source name for the Sources page."""
    from urllib.parse import urlparse
    sources = config_files.get_recipe_sources().get("sources", [])
    result = {}
    all_stubs = db.query(models.Recipe).filter(
        models.Recipe.source_url.isnot(None)
    ).all()
    for src in sources:
        name = src.get("name", "")
        cat_urls = src.get("category_urls", [])
        domains = set()
        for cu in cat_urls:
            try:
                domains.add(urlparse(cu).netloc)
            except Exception:
                pass
        count = sum(
            1 for s in all_stubs
            if s.source_url and not s.source_url.startswith("mealie:")
            and urlparse(s.source_url).netloc in domains
        )
        result[name] = count
    return result


@router.delete("/source-stubs")
def delete_source_stubs(source_name: str, db: Session = Depends(get_db)):
    """Delete all recipe stubs from a named source."""
    from urllib.parse import urlparse
    sources = config_files.get_recipe_sources().get("sources", [])
    src = next((s for s in sources if s.get("name") == source_name), None)
    domains = set()
    if src:
        for cu in src.get("category_urls", []):
            try: domains.add(urlparse(cu).netloc)
            except Exception: pass
    else:
        bare = source_name.lstrip("www.")
        domains.add(bare)
        domains.add("www." + bare)
    stubs = db.query(models.Recipe).filter(
        models.Recipe.source_url.isnot(None),
        models.Recipe.mealie_slug.is_(None),
    ).all()
    to_delete = [s for s in stubs
                 if not s.source_url.startswith("mealie:")
                 and urlparse(s.source_url).netloc in domains]
    count = len(to_delete)
    for s in to_delete:
        db.delete(s)
    db.commit()
    log.info("Deleted %d stubs for source '%s'", count, source_name)
    return {"deleted": count, "source": source_name}

# ── Protein categories ────────────────────────────────────────────────────────

@router.get("/protein-categories")
def get_protein_categories():
    return {"categories": config_files.get_protein_categories()}


@router.put("/protein-categories")
def save_protein_categories(payload: dict):
    cats = payload.get("categories", [])
    config_files.save_protein_categories(cats)
    return {"categories": cats}
