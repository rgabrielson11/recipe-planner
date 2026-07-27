"""
Config router — Phase 8
Exposes the YAML configuration files as editable API resources.
All writes use ruamel.yaml round-trip mode so comments are preserved.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config_files

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
    disc["min_scraped_rating"]     = payload.min_scraped_rating
    disc["min_scraped_reviews"]    = payload.min_scraped_reviews
    disc["stub_rescrape_days"]     = payload.stub_rescrape_days
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
