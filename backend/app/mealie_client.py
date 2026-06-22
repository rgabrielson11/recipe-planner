"""
Thin wrapper around the Mealie REST API.

Endpoints/behavior confirmed against Mealie's own docs and source
(docs.mealie.io, mealie-recipes/mealie):
  - POST {base}/api/recipes/create/url   {"url": "..."}  -> returns the new
    recipe's slug as a raw JSON string. This is how URL-based recipe import
    works; Mealie does the actual scraping (it uses the same recipe-scrapers
    library this project considered building on directly).
  - GET  {base}/api/recipes/{slug}       -> full recipe detail
  - GET  {base}/api/recipes              -> paginated list, supports
    page/perPage/orderBy/orderDirection/queryFilter query params
  - PATCH {base}/api/recipes/{slug}      -> partial update (e.g. rating)
  - Auth: `Authorization: Bearer <token>` on every request

Mealie's exact schema (field names like `rating`, favorites-by-user vs a
recipe-level rating field) can shift between versions — this client keeps
calls narrow and wraps everything in try/except so a Mealie hiccup never
breaks local pantry/preference/meal-plan functionality. Verify field names
against your own instance's interactive docs at
http://<your-mealie-host>:<port>/docs if rating sync misbehaves.
"""

import os
from typing import Optional

import requests

MEALIE_BASE_URL = os.getenv("MEALIE_BASE_URL", "").rstrip("/")
MEALIE_API_TOKEN = os.getenv("MEALIE_API_TOKEN", "")

_TIMEOUT_SECONDS = 15


class MealieError(Exception):
    """Raised on a Mealie API failure. Callers should catch this and
    degrade gracefully — Mealie being down shouldn't break local features."""


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MEALIE_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _check_configured():
    if not MEALIE_BASE_URL or not MEALIE_API_TOKEN:
        raise MealieError(
            "MEALIE_BASE_URL / MEALIE_API_TOKEN not configured — set both in .env"
        )


def import_recipe_from_url(url: str) -> str:
    """Imports a recipe into Mealie from a URL. Returns the new recipe's slug."""
    _check_configured()
    try:
        resp = requests.post(
            f"{MEALIE_BASE_URL}/api/recipes/create/url",
            headers=_headers(),
            json={"url": url},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        # Mealie returns the slug as a raw JSON string, e.g. "some-recipe-slug"
        return resp.json()
    except requests.RequestException as e:
        raise MealieError(f"Failed to import recipe from {url}: {e}") from e


def get_recipe(slug: str) -> dict:
    _check_configured()
    try:
        resp = requests.get(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}", headers=_headers(), timeout=_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise MealieError(f"Failed to fetch recipe {slug}: {e}") from e


def list_recipes(
    query_filter: Optional[str] = None,
    tag_name: Optional[str] = None,
    order_by: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    """
    Lists recipes from Mealie with optional server-side filtering.

    tag_name:     When provided, adds a Mealie queryFilter clause to restrict
                  results to recipes that carry this tag. The matching engine
                  then double-checks client-side because Mealie's queryFilter
                  syntax for tag arrays can vary between versions.

    query_filter: Any additional Mealie filter expression (ANDed with the tag
                  filter if both are supplied). e.g. 'rating >= 4'.

    See https://docs.mealie.io/documentation/getting-started/api-usage/ for
    the full Mealie filter query syntax.
    """
    _check_configured()
    params: dict = {"page": page, "perPage": per_page}

    # Build the combined filter expression
    filter_parts: list[str] = []
    if tag_name:
        # Mealie uses case-insensitive LIKE matching on tag names via queryFilter.
        # Exact match: tags.name = "dinner-planner"
        filter_parts.append(f'tags.name = "{tag_name}"')
    if query_filter:
        filter_parts.append(query_filter)
    if filter_parts:
        params["queryFilter"] = " AND ".join(filter_parts)

    if order_by:
        params["orderBy"] = order_by

    try:
        resp = requests.get(
            f"{MEALIE_BASE_URL}/api/recipes", headers=_headers(),
            params=params, timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise MealieError(f"Failed to list recipes: {e}") from e


def recipe_has_tag(recipe_detail: dict, tag_name: str) -> bool:
    """
    Client-side check: returns True if the recipe detail dict contains a tag
    whose name matches tag_name (case-insensitive). Used as a fallback
    double-check after list_recipes() since queryFilter behavior for nested
    arrays can vary across Mealie versions.
    """
    tag_lower = tag_name.lower()
    for tag in recipe_detail.get("tags", []):
        name = tag.get("name", "") if isinstance(tag, dict) else str(tag)
        if name.lower() == tag_lower:
            return True
    return False


def set_recipe_rating(slug: str, rating: int) -> None:
    """Best-effort sync of our local star rating back to Mealie."""
    _check_configured()
    try:
        resp = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            json={"rating": rating},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise MealieError(f"Failed to set rating for {slug}: {e}") from e
