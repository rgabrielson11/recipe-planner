"""
Thin Mealie REST API wrapper — Phase 9

Phase 9 fixes:
  • add_tag_to_recipe now uses GET /api/organizers/tags to find or create the
    tag object (with its ID) before PATCHing the recipe. Mealie requires the
    full {id, name} tag object; passing {name} only causes 422.
  • import_recipe_from_url strips any cost-related fields from ingredients
    after import so Mealie shows clean ingredient lists.
"""

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

MEALIE_BASE_URL  = os.getenv("MEALIE_BASE_URL",  "").rstrip("/")
MEALIE_API_TOKEN = os.getenv("MEALIE_API_TOKEN", "")
_TIMEOUT = 20


class MealieError(Exception):
    """Raised on any Mealie API failure."""


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MEALIE_API_TOKEN}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def _check_configured() -> None:
    if not MEALIE_BASE_URL or not MEALIE_API_TOKEN:
        raise MealieError(
            "MEALIE_BASE_URL / MEALIE_API_TOKEN not configured — set both in .env"
        )


# ── Tags ──────────────────────────────────────────────────────────────────────

def _get_or_create_tag(tag_name: str) -> dict:
    """
    Return {id, name} for tag_name, creating it via the organizers API if needed.

    Mealie requires the tag's database ID when patching a recipe's tag list.
    Sending only {name} without ID returns 422 Unprocessable Entity.
    """
    _check_configured()
    # Search for existing tag
    try:
        r = requests.get(
            f"{MEALIE_BASE_URL}/api/organizers/tags",
            headers=_headers(),
            params={"search": tag_name, "perPage": 20},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        for tag in r.json().get("items", []):
            if tag.get("name", "").lower() == tag_name.lower():
                log.debug("Mealie tag found: '%s' id=%s", tag_name, tag["id"])
                return {"id": tag["id"], "name": tag["name"]}
    except requests.RequestException as e:
        raise MealieError(f"Failed to search tags: {e}") from e

    # Create the tag
    try:
        r = requests.post(
            f"{MEALIE_BASE_URL}/api/organizers/tags",
            headers=_headers(),
            json={"name": tag_name},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        tag = r.json()
        log.info("Mealie tag created: '%s' id=%s", tag_name, tag["id"])
        return {"id": tag["id"], "name": tag["name"]}
    except requests.RequestException as e:
        raise MealieError(f"Failed to create tag '{tag_name}': {e}") from e


def add_tag_to_recipe(slug: str, tag_name: str) -> None:
    """
    Add tag_name to an existing Mealie recipe without disturbing existing tags.
    Looks up / creates the tag to get its ID, then PATCHes the recipe.
    """
    _check_configured()
    try:
        tag_obj = _get_or_create_tag(tag_name)
        detail  = get_recipe(slug)

        current_tags = detail.get("tags", [])
        for existing in current_tags:
            if isinstance(existing, dict):
                if existing.get("id") == tag_obj["id"] or \
                   existing.get("name", "").lower() == tag_name.lower():
                    log.debug("Tag '%s' already on recipe '%s'", tag_name, slug)
                    return  # already tagged

        new_tags = current_tags + [tag_obj]
        r = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            json={"tags": new_tags},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        log.info("Tagged recipe '%s' with '%s'", slug, tag_name)
    except requests.RequestException as e:
        raise MealieError(f"Failed to add tag '{tag_name}' to {slug}: {e}") from e


# ── Import ────────────────────────────────────────────────────────────────────

_COST_FIELDS = {"costPerServing", "cost", "price", "unitCost", "unitPrice",
                "totalCost", "ingredientCost"}


def _strip_cost_fields(detail: dict) -> dict:
    """
    Remove cost-related fields from a Mealie recipe detail dict.
    Called after URL import to keep ingredient lists clean.
    """
    cleaned_ings = []
    for ing in detail.get("recipeIngredient", []):
        if isinstance(ing, dict):
            cleaned_ings.append({k: v for k, v in ing.items() if k not in _COST_FIELDS})
        else:
            cleaned_ings.append(ing)
    if cleaned_ings != detail.get("recipeIngredient"):
        detail = {**detail, "recipeIngredient": cleaned_ings}
    return {k: v for k, v in detail.items() if k not in _COST_FIELDS}


def import_recipe_from_url(url: str) -> str:
    """
    Import a recipe from URL into Mealie via the URL importer.
    Returns the new recipe slug.
    After import, strips any cost fields from ingredients.
    """
    _check_configured()
    try:
        r = requests.post(
            f"{MEALIE_BASE_URL}/api/recipes/create/url",
            headers=_headers(),
            json={"url": url, "includeTags": False},
            timeout=30,
        )
        r.raise_for_status()
        slug = r.json()   # Mealie returns the slug as a bare JSON string
        log.info("Mealie URL import: '%s' → slug='%s'", url, slug)
    except requests.RequestException as e:
        raise MealieError(f"Failed to import {url}: {e}") from e

    # Strip cost fields from the imported recipe
    try:
        detail  = get_recipe(slug)
        cleaned = _strip_cost_fields(detail)
        if cleaned != detail:
            r2 = requests.patch(
                f"{MEALIE_BASE_URL}/api/recipes/{slug}",
                headers=_headers(),
                json={"recipeIngredient": cleaned.get("recipeIngredient", [])},
                timeout=_TIMEOUT,
            )
            r2.raise_for_status()
            log.debug("Stripped cost fields from '%s'", slug)
    except Exception as e:
        log.debug("Cost-strip step skipped for '%s': %s", slug, e)

    return slug


# ── Recipe CRUD ───────────────────────────────────────────────────────────────

def get_recipe(slug: str) -> dict:
    """Returns full recipe detail for a given slug."""
    _check_configured()
    try:
        r = requests.get(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise MealieError(f"Failed to fetch recipe {slug}: {e}") from e


def list_recipes(
    tag_name: Optional[str] = None,
    query_filter: Optional[str] = None,
    order_by: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    _check_configured()
    params: dict = {"page": page, "perPage": per_page}
    filter_parts: list[str] = []
    if tag_name:
        filter_parts.append(f'tags.name = "{tag_name}"')
    if query_filter:
        filter_parts.append(query_filter)
    if filter_parts:
        params["queryFilter"] = " AND ".join(filter_parts)
    if order_by:
        params["orderBy"] = order_by
    try:
        r = requests.get(
            f"{MEALIE_BASE_URL}/api/recipes",
            headers=_headers(), params=params, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise MealieError(f"Failed to list recipes: {e}") from e


def recipe_has_tag(detail: dict, tag_name: str) -> bool:
    tag_lower = tag_name.lower()
    for tag in detail.get("tags", []):
        name = tag.get("name", "") if isinstance(tag, dict) else str(tag)
        if name.lower() == tag_lower:
            return True
    return False


def set_recipe_rating(slug: str, rating: int) -> None:
    _check_configured()
    try:
        r = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(), json={"rating": rating}, timeout=_TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise MealieError(f"Failed to set rating for {slug}: {e}") from e


def get_top_rated_recipes(min_rating: int = 4, page: int = 1, per_page: int = 20) -> list[dict]:
    _check_configured()
    try:
        result = list_recipes(
            query_filter=f"rating >= {min_rating}",
            order_by="rating",
            page=page,
            per_page=per_page,
        )
        return result.get("items", [])
    except MealieError:
        return []
