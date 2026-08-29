"""
Thin Mealie REST API wrapper — Phase 9

Phase 9 fixes:
  • add_tag_to_recipe now uses GET /api/organizers/tags to find or create the
    tag object (with its ID) before PATCHing the recipe. Mealie requires the
    full {id, name} tag object; passing {name} only causes 422.
  • import_recipe_from_url strips any cost-related fields from ingredients
    after import so Mealie shows clean ingredient lists.

Phase 9 patch — full-body PATCH fix:
  • Mealie's PATCH /api/recipes/{slug} has PUT semantics in practice: it
    replaces the entire recipe with whatever body is sent.  Sending only a
    partial payload (e.g. {"tags": […]} or {"recipeIngredient": […]}) causes
    a 422 Unprocessable Entity because required fields are absent.
  • add_tag_to_recipe now mutates the tags key on the full detail dict
    returned by get_recipe() and PATCHes the complete body.
  • The cost-strip PATCH in import_recipe_from_url now sends the full
    cleaned detail dict rather than just the recipeIngredient field.
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


def _escape_filter_value(value: str) -> str:
    """
    Escape a value for safe interpolation into a Mealie queryFilter string
    (e.g. `orgURL = "{value}"`). Mealie's filter language treats `"` as the
    string delimiter, so an unescaped `"` in a URL or tag name lets the
    value break out of its quotes and inject additional filter clauses.
    Backslash-escaping the delimiter mirrors how the underlying filter
    grammar expects quotes to be escaped.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MEALIE_API_TOKEN}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def is_configured() -> bool:
    """Returns True if Mealie credentials are set."""
    return bool(MEALIE_BASE_URL and MEALIE_API_TOKEN)


def _check_configured() -> None:
    if not is_configured():
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
        # Mealie's PATCH /api/recipes/{slug} has PUT semantics — it replaces
        # the entire recipe with whatever is sent.  Sending only {"tags": …}
        # causes a 422 because required fields are missing.  We must send the
        # full detail dict with the tags field updated in-place.
        detail["tags"] = new_tags
        r = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            json=detail,
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


def find_recipe_by_url(url: str) -> Optional[str]:
    """
    Search Mealie for a recipe whose orgURL matches url.
    Returns the slug if found, None otherwise.

    Used as a dedup guard before importing: if Mealie already holds this
    recipe (e.g. from a previous run or a DB reset) we return the existing
    slug instead of creating a duplicate.
    """
    _check_configured()
    try:
        r = requests.get(
            f"{MEALIE_BASE_URL}/api/recipes",
            headers=_headers(),
            params={"queryFilter": f'orgURL = "{_escape_filter_value(url)}"', "perPage": 5},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            slug = items[0].get("slug")
            log.info("find_recipe_by_url: found existing slug='%s' for %s", slug, url)
            return slug
    except requests.RequestException as e:
        log.debug("find_recipe_by_url: query failed for %s: %s", url, e)
    return None


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
            # Same PUT-semantics constraint — must send the full recipe body.
            r2 = requests.patch(
                f"{MEALIE_BASE_URL}/api/recipes/{slug}",
                headers=_headers(),
                json=cleaned,
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
        filter_parts.append(f'tags.name = "{_escape_filter_value(tag_name)}"')
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


# ── Categories ────────────────────────────────────────────────────────────────

def _get_or_create_category(name: str) -> dict:
    """
    Return {id, name, slug} for a category, creating it if it doesn't exist.
    Same pattern as _get_or_create_tag — Mealie requires the ID when patching.
    """
    _check_configured()
    try:
        r = requests.get(
            f"{MEALIE_BASE_URL}/api/organizers/categories",
            headers=_headers(),
            params={"search": name, "perPage": 20},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        for cat in r.json().get("items", []):
            if cat.get("name", "").lower() == name.lower():
                log.debug("Mealie category found: '%s' id=%s", name, cat["id"])
                return {"id": cat["id"], "name": cat["name"], "slug": cat.get("slug", "")}
    except requests.RequestException as e:
        log.debug("Category search failed for '%s': %s", name, e)

    try:
        r = requests.post(
            f"{MEALIE_BASE_URL}/api/organizers/categories",
            headers=_headers(),
            json={"name": name},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        cat = r.json()
        log.info("Mealie category created: '%s' id=%s", name, cat["id"])
        return {"id": cat["id"], "name": cat["name"], "slug": cat.get("slug", "")}
    except requests.RequestException as e:
        raise MealieError(f"Failed to create category '{name}': {e}") from e


def set_recipe_categories(slug: str, category_names: list[str]) -> None:
    """
    Set recipe categories on a Mealie recipe, merging with any existing ones.
    Categories are looked up / created as needed.
    Best-effort — logs and returns quietly on failure.
    """
    _check_configured()
    try:
        detail = get_recipe(slug)
        existing = {c["name"].lower() for c in (detail.get("recipeCategory") or [])}
        new_cats = []
        for name in category_names:
            if name.lower() in existing:
                continue
            try:
                new_cats.append(_get_or_create_category(name))
            except MealieError as e:
                log.debug("Skipping category '%s': %s", name, e)
        if not new_cats:
            return
        detail["recipeCategory"] = list(detail.get("recipeCategory") or []) + new_cats
        r = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            json=detail,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        log.info("Set categories %s on '%s'", [c['name'] for c in new_cats], slug)
    except Exception as e:
        log.debug("set_recipe_categories skipped for '%s': %s", slug, e)


# ── Meal plan ─────────────────────────────────────────────────────────────────

def add_to_mealie_meal_plan(slug: str, date_str: str, entry_type: str = "dinner") -> None:
    """
    Add a recipe to Mealie's meal planner for the given date.
    date_str should be ISO format YYYY-MM-DD (Monday of the planning week).
    Best-effort — logs at INFO so failures are visible.
    """
    _check_configured()
    try:
        # Get the recipe's Mealie UUID from its slug
        detail = get_recipe(slug)
        recipe_id = detail.get("id")
        if not recipe_id:
            log.warning("add_to_mealie_meal_plan: no id for slug=%s", slug)
            return

        # Check if an entry for this recipe+date already exists
        try:
            existing = requests.get(
                f"{MEALIE_BASE_URL}/api/households/mealplans",
                headers=_headers(),
                params={"start_date": date_str, "end_date": date_str},
                timeout=_TIMEOUT,
            )
            if existing.ok:
                entries = existing.json().get("items", [])
                for e in entries:
                    if e.get("recipeId") == recipe_id or e.get("recipe_id") == recipe_id:
                        log.info("Meal plan entry already exists for '%s' on %s", slug, date_str)
                        return
        except Exception as _ce:
            log.debug("Meal plan duplicate check failed: %s", _ce)

        # Mealie v1.x uses camelCase in JSON body
        payload = {
            "date":      date_str,
            "entryType": entry_type,
            "recipeId":  recipe_id,
            "title":     detail.get("name", slug),
        }
        r = requests.post(
            f"{MEALIE_BASE_URL}/api/households/mealplans",
            headers=_headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
        if not r.ok:
            log.warning("Meal plan add failed for '%s' (%s): %s — body: %s",
                        slug, date_str, r.status_code, r.text[:300])
            return
        log.info("Added '%s' to Mealie meal plan for %s", detail.get("name", slug), date_str)
    except Exception as e:
        log.warning("add_to_mealie_meal_plan failed for slug=%s: %s", slug, e)


def patch_recipe_ingredients(slug: str, ingredient_strings: list[str], base_servings: Optional[float] = None) -> None:
    """
    Re-parse our scraped ingredient strings and update the Mealie recipe's
    ingredient quantities/units in-place, preserving Mealie's food references.
    Only updates quantity=0 or null ingredients — leaves correctly parsed ones alone.
    """
    _check_configured()
    import re as _re

    _FRAC = {"¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1/3, "⅔": 2/3,
             "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
    _UNIT_MAP = {
        "tsp": "teaspoon", "tbsp": "tablespoon", "tablespoons": "tablespoon",
        "teaspoons": "teaspoon", "cups": "cup", "oz": "ounce", "ounces": "ounce",
        "lbs": "pound", "lb": "pound", "pounds": "pound", "g": "gram",
        "grams": "gram", "kg": "kilogram", "ml": "milliliter", "l": "liter",
        "cloves": "clove",
    }
    _ING_RE = _re.compile(
        r"^\s*(?P<qty>[\d¼½¾⅓⅔⅛⅜⅝⅞]+(?:[\.\/]\d+)?)"
        r"\s*(?P<unit>cups?|tbsp|tsp|tablespoons?|teaspoons?|lbs?|oz|g|kg|ml|l|"
        r"ounces?|pounds?|grams?|cloves?)?"
        r"(?:\(s\))?\s",
        _re.IGNORECASE,
    )

    def _qty(s: str) -> Optional[float]:
        for ch, v in _FRAC.items():
            if ch in s:
                rest = s.replace(ch, "").strip()
                try: return v + float(rest) if rest else v
                except ValueError: return v
        try:
            if "/" in s:
                n, d = s.split("/", 1)
                return float(n.strip()) / float(d.strip())
            return float(s.strip())
        except (ValueError, ZeroDivisionError):
            return None

    # Build a lookup from our parsed strings: index → (qty, unit)
    parsed: list[tuple] = []
    for raw in ingredient_strings:
        m = _ING_RE.match(raw.strip())
        if m:
            q = _qty(m.group("qty"))
            u_raw = m.group("unit")
            u = _UNIT_MAP.get((u_raw or "").lower(), u_raw)
            if u and u.lower() in ("unit", "units", "each"):
                u = None
            parsed.append((q, u))
        else:
            parsed.append((None, None))

    try:
        detail = get_recipe(slug)
        mealie_ings = detail.get("recipeIngredient", [])
        changed = 0

        for i, ing in enumerate(mealie_ings):
            if i >= len(parsed):
                break
            q, u = parsed[i]
            if q is None:
                continue

            cur_qty = ing.get("quantity")
            # Only update if Mealie has 0 or null quantity
            if cur_qty and cur_qty != 0.0:
                continue

            ing["quantity"] = q
            # Only update unit if Mealie has none and we parsed one
            if u and ing.get("unit") is None:
                ing["unit"] = {"name": u}
            changed += 1

        if changed == 0:
            log.info("patch_recipe_ingredients: all quantities already set for '%s'", slug)
            return

        if base_servings and base_servings > 0 and not detail.get("recipeServings"):
            detail["recipeServings"] = base_servings

        r = requests.patch(
            f"{MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_headers(),
            json=detail,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        log.info("Patched %d ingredient quantities on Mealie recipe '%s'", changed, slug)
    except Exception as e:
        log.warning("patch_recipe_ingredients failed for slug=%s: %s", slug, e)


