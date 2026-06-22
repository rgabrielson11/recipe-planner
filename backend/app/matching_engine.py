"""
Matching Engine — Phase 3 (updated for flat-list output and temporary suppressions)

Scores every dinner-tagged Mealie recipe against the household's pantry,
long-term preferences, and this week's specific intent (ingredient hints),
then returns a flat ranked list of `num_suggestions` recipes for the household
to review and choose from.

Scoring (max ~145 pts):
  pantry overlap       0–50   (% of ingredients on hand + staples)
  weekly hints         0–45   (+15/hint — stronger than permanent liked_items)
  liked items          0–20   (+5/item — permanent long-term preference)
  soft dislikes        0– –45 (−15/item)
  cook time over max   −20    (soft penalty, not a hard filter)
  favorite bonus       +25    (previously rated ≥ threshold)

Hard rejections (recipe never shown):
  • excluded_items keyword in recipe text (allergy / never-make)
  • required method not in available_methods (if list non-empty)
  • permanently rejected by this household
  • temporarily rejected AND still within the suppression window

Tag filtering:
  • Only recipes with the household's mealie_dinner_tag are fetched/scored.
  • Passed as Mealie server-side queryFilter AND double-checked client-side.
  • Tag "" disables filtering (use all Mealie recipes).
"""

import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models, mealie_client, config_files

# ── Scoring weights ───────────────────────────────────────────────────────────
_PANTRY_MAX_PTS       = 50
_WEEKLY_HINT_PTS_EACH = 15
_WEEKLY_HINT_MAX_PTS  = 45
_LIKED_PTS_EACH       = 5
_LIKED_MAX_PTS        = 20
_DISLIKE_PTS_EACH     = 15
_DISLIKE_MAX_PENALTY  = 45
_COOK_TIME_PENALTY    = 20
_FAVORITE_BONUS       = 25

_HARD_REJECT = float("-inf")

# ── Cooking method → keyword map (for available_methods hard-filter) ──────────
_METHOD_KEYWORDS: dict[str, list[str]] = {
    "slow_cooker":                 ["slow cooker", "crockpot", "crock pot", "slow-cooker"],
    "instant_pot_pressure_cooker": ["instant pot", "pressure cooker", "electric pressure"],
    "air_fryer":                   ["air fryer", "air-fryer", "airfryer"],
    "sous_vide":                   ["sous vide", "sous-vide"],
    "smoker":                      ["smoked", "smoker", "smoke for", "low and slow"],
    "grill":                       ["grill", "grilled", "barbecue", "bbq", "char-grilled"],
    "oven":                        ["bake", "baked", "roast", "roasted", "broil", "broiled", "braise"],
    "stovetop":                    ["sauté", "saute", "pan-fry", "stir-fry", "stir fry", "sear",
                                    "simmer", "skillet"],
    "microwave":                   ["microwave"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.lower().strip()


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


def _recipe_text(detail: dict) -> str:
    parts: list[str] = [
        detail.get("name", ""),
        detail.get("description", ""),
    ]
    cuisine = detail.get("recipeCuisine")
    if isinstance(cuisine, str):
        parts.append(cuisine)
    for tag in detail.get("tags", []):
        parts.append(tag.get("name", "") if isinstance(tag, dict) else str(tag))
    for cat in detail.get("recipeCategory", []):
        parts.append(cat.get("name", "") if isinstance(cat, dict) else str(cat))
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict):
            continue
        parts.append(ing.get("note", "") or "")
        food = ing.get("food")
        if food:
            parts.append(food.get("name", "") if isinstance(food, dict) else str(food))
    return " ".join(p for p in parts if p)


def _ingredient_names(detail: dict) -> set[str]:
    names: set[str] = set()
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict):
            continue
        food = ing.get("food")
        if food:
            n = food.get("name", "") if isinstance(food, dict) else str(food)
            if n:
                names.add(_norm(n))
        note = ing.get("note", "")
        if note:
            names.add(_norm(note))
    return names


def _parse_duration_minutes(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", raw, re.IGNORECASE)
    if not m:
        return None
    total = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return total if total > 0 else None


def _required_methods(detail: dict) -> set[str]:
    text = _recipe_text(detail).lower()
    return {m for m, kws in _METHOD_KEYWORDS.items() if any(k in text for k in kws)}


# ── Suppression logic ─────────────────────────────────────────────────────────

def _build_suppressed_ids(
    household_id: str,
    current_week: date,
    db: Session,
) -> tuple[set[str], set[str]]:
    """
    Returns (permanently_rejected_ids, temporarily_suppressed_ids).
    Temporary suppressions whose window has expired are ignored (recipe resurfaces).
    """
    permanent:  set[str] = set()
    suppressed: set[str] = set()

    rejections = db.query(models.RecipeRejection).filter(
        models.RecipeRejection.household_id == household_id
    ).all()

    for r in rejections:
        if r.is_permanent:
            permanent.add(r.recipe_id)
        else:
            if r.rejected_week and r.suppress_weeks:
                resurface_week = r.rejected_week + timedelta(weeks=r.suppress_weeks)
                if current_week < resurface_week:
                    suppressed.add(r.recipe_id)
            # If rejected_week or suppress_weeks missing, treat as expired → resurface

    return permanent, suppressed


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(
    detail: dict,
    pantry_set: set[str],
    staples_set: set[str],
    prefs: Optional[models.Preference],
    weekly_hints: list[str],
) -> tuple[float, float, list[str]]:
    """Returns (score, pantry_overlap_pct, missing_ingredients). _HARD_REJECT drops recipe."""
    all_on_hand = pantry_set | staples_set
    ing_names   = _ingredient_names(detail)

    if ing_names:
        on_hand     = {n for n in ing_names if any(_contains(o, n) or _contains(n, o) for o in all_on_hand)}
        missing     = [n for n in ing_names if n not in on_hand]
        overlap_pct = len(on_hand) / len(ing_names)
    else:
        on_hand = set(); missing = []; overlap_pct = 0.0

    score = overlap_pct * _PANTRY_MAX_PTS
    text  = _recipe_text(detail)

    if prefs:
        # Hard excludes
        for excl in (prefs.excluded_items or []):
            if _contains(text, excl):
                return _HARD_REJECT, overlap_pct, missing

        # Method hard-filter
        if prefs.available_methods:
            unavail = _required_methods(detail) - set(prefs.available_methods)
            if unavail:
                return _HARD_REJECT, overlap_pct, missing

        # Soft dislikes
        penalty = min(sum(_DISLIKE_PTS_EACH for d in (prefs.disliked_items or []) if _contains(text, d)),
                      _DISLIKE_MAX_PENALTY)
        score -= penalty

        # Liked items
        bonus = min(sum(_LIKED_PTS_EACH for li in (prefs.liked_items or []) if _contains(text, li)),
                    _LIKED_MAX_PTS)
        score += bonus

        # Cook time
        if prefs.max_cook_time_minutes:
            mins = _parse_duration_minutes(detail.get("totalTime") or detail.get("performTime"))
            if mins and mins > prefs.max_cook_time_minutes:
                score -= _COOK_TIME_PENALTY

    # Weekly hints (applied regardless of whether prefs exist)
    hint_bonus = min(sum(_WEEKLY_HINT_PTS_EACH for h in weekly_hints if _contains(text, h)),
                     _WEEKLY_HINT_MAX_PTS)
    score += hint_bonus

    return score, overlap_pct, missing


# ── Public entry point ────────────────────────────────────────────────────────

def build_suggestions(
    household_id: str,
    week_start: date,
    db: Session,
    num_override: Optional[int] = None,
) -> dict:
    """
    Returns a flat ranked suggestion list for the given week.

    num_suggestions is resolved in priority order:
      1. num_override (from query param)
      2. WeeklyIntent.num_suggestions (set during weekly check-in)
      3. Preference.default_num_suggestions
      4. Hard default: 10
    """
    household = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not household:
        raise ValueError(f"Household {household_id!r} not found")

    prefs  = db.query(models.Preference).filter(
        models.Preference.household_id == household_id
    ).first()
    intent = db.query(models.WeeklyIntent).filter(
        models.WeeklyIntent.household_id == household_id,
        models.WeeklyIntent.week_start_date == week_start,
    ).first()

    n = (
        num_override
        or (intent.num_suggestions if intent and intent.num_suggestions else None)
        or (prefs.default_num_suggestions if prefs else None)
        or 10
    )
    dinner_tag   = (prefs.mealie_dinner_tag if prefs else "") or ""
    weekly_hints = list(intent.ingredient_hints) if intent else []

    # Pantry + staples
    pantry_rows = db.query(models.PantryItem).filter(
        models.PantryItem.household_id == household_id
    ).all()
    pantry_set  = {_norm(p.name) for p in pantry_rows}
    staples_set = {_norm(s) for s in config_files.get_staples()}

    # Rejection windows
    perm_ids, supp_ids = _build_suppressed_ids(household_id, week_start, db)
    excluded_ids = perm_ids | supp_ids

    # Favorite recipe IDs
    fav_ids = {
        e.recipe_id
        for e in db.query(models.MealPlanEntry).filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.is_favorite.is_(True),
        ).all()
    }

    # Local recipe index
    local_by_slug = {
        r.mealie_slug: r
        for r in db.query(models.Recipe).filter(models.Recipe.mealie_slug.isnot(None)).all()
    }

    # Score all Mealie recipes
    scored: list[dict] = []
    mealie_ok = True

    try:
        page = 1
        while True:
            result = mealie_client.list_recipes(
                tag_name=dinner_tag or None,
                page=page,
                per_page=100,
            )
            items = result.get("items", [])
            if not items:
                break

            for summary in items:
                slug = summary.get("slug")
                if not slug:
                    continue
                local     = local_by_slug.get(slug)
                recipe_id = local.id if local else None

                if recipe_id and recipe_id in excluded_ids:
                    continue

                try:
                    detail = mealie_client.get_recipe(slug)
                except mealie_client.MealieError:
                    continue

                if dinner_tag and not mealie_client.recipe_has_tag(detail, dinner_tag):
                    continue

                s, overlap, missing = _score(detail, pantry_set, staples_set, prefs, weekly_hints)
                if s == _HARD_REJECT:
                    continue

                is_fav = bool(recipe_id and recipe_id in fav_ids)
                if is_fav:
                    s += _FAVORITE_BONUS

                scored.append({
                    "recipe_id":           recipe_id,
                    "title":               detail.get("name", slug),
                    "mealie_slug":         slug,
                    "source_url":          local.source_url if local else "",
                    "score":               round(s, 1),
                    "pantry_overlap_pct":  round(overlap * 100, 1),
                    "missing_ingredients": missing[:15],
                    "is_favorite":         is_fav,
                    "total_time_minutes":  _parse_duration_minutes(
                        detail.get("totalTime") or detail.get("performTime")
                    ),
                })

            if page >= result.get("totalPages", 1):
                break
            page += 1

    except mealie_client.MealieError:
        mealie_ok = False
        for recipe in db.query(models.Recipe).all():
            if recipe.id in excluded_ids:
                continue
            is_fav = recipe.id in fav_ids
            scored.append({
                "recipe_id": recipe.id, "title": recipe.title,
                "mealie_slug": recipe.mealie_slug or "", "source_url": recipe.source_url,
                "score": float(_FAVORITE_BONUS if is_fav else 0),
                "pantry_overlap_pct": 0.0, "missing_ingredients": [],
                "is_favorite": is_fav, "total_time_minutes": None,
            })

    # Sort: favorites first within score tiers, then by score descending
    scored.sort(key=lambda r: (-r["score"], not r["is_favorite"]))
    top = scored[:n]

    fav_pool  = [r for r in scored if r["is_favorite"]]
    disc_pool = [r for r in scored if not r["is_favorite"]]

    return {
        "week_start_date":     week_start.isoformat(),
        "household_id":        household_id,
        "num_suggestions":     n,
        "mealie_available":    mealie_ok,
        "dinner_tag_filter":   dinner_tag,
        "weekly_hints_applied": weekly_hints,
        "favorites_in_pool":   len(fav_pool),
        "discoveries_in_pool": len(disc_pool),
        "suggestions": [
            {"rank": i + 1, **r} for i, r in enumerate(top)
        ],
    }
