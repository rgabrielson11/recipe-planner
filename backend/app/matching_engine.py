"""
Matching Engine — Phase 10
=========================

Phase 7 changes vs Phase 6:
  1. dinner_tag is now a SOFT BOOST (+10 pts) instead of a hard filter.
     Previously, Mealie recipes that lacked the dinner tag were silently
     dropped from Pool A; this meant that if no Mealie recipes had the
     tag applied, Pool A was always empty.  Now every Mealie recipe rated
     ≥ mealie_min_rating is eligible; tagged ones simply score higher.

  2. Pool B (discovered) now always fills up to n suggestions regardless
     of Pool A size.  Previously the code computed discovery_slots =
     n - mealie_fav_cnt which could leave discovery capped lower than needed
     when Pool A returned fewer than mealie_fav_cnt results.  Now discovery
     is asked for n - len(actual_mealie_favs) + 10 overshoot.

  3. Verbose DEBUG logging throughout for troubleshooting via /api/logs.

Two recipe pools (unchanged from Phase 6):
  POOL A — Mealie proven favourites (default up to 2 slots)
    Mealie recipes rated ≥ mealie_min_rating (default 4★).
    Tagged with dinner_tag receive an extra +10 pts boost.
    Proven winners the household already loves.

  POOL B — Newly discovered recipes (fills remaining slots)
    Scraped from curated sites via recipe_discovery.  Mealie slug is None
    until the household confirms a selection, then imported automatically.

Scoring (max ~155 pts):
  pantry overlap       0–50   % of ingredients on hand + staples
  weekly hints         0–45   +15/hint keyword
  liked items          0–20   +5/liked_item
  soft dislikes        0– –45 –15/item
  cook time            –20    penalty if over household max
  favourite bonus      +25    for Mealie proven favourites (Pool A)
  dinner tag bonus     +10    for Mealie recipes tagged with dinner_tag

Hard filters (both pools):
  • excluded_items keyword in recipe text
  • required cook method not in available_methods
  • recipe rejected permanently or within suppression window
"""

import logging
import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models, mealie_client, config_files
from app import recipe_discovery

log = logging.getLogger(__name__)

# ── Scoring weights ────────────────────────────────────────────────────────────
_PANTRY_MAX_PTS       = 50
_WEEKLY_HINT_PTS_EACH = 15
_WEEKLY_HINT_MAX_PTS  = 45
_LIKED_PTS_EACH       = 5
_LIKED_MAX_PTS        = 20
_DISLIKE_PTS_EACH     = 15
_DISLIKE_MAX_PENALTY  = 45
_COOK_TIME_PENALTY    = 20
_FAVORITE_BONUS       = 25    # Pool A: proven Mealie favourite
_DINNER_TAG_BONUS     = 10    # Soft boost for dinner-tagged Mealie recipes
_HARD_REJECT          = float("-inf")

_METHOD_KEYWORDS: dict[str, list[str]] = {
    "slow_cooker":                 ["slow cooker", "crockpot", "crock pot"],
    "instant_pot_pressure_cooker": ["instant pot", "pressure cooker"],
    "air_fryer":                   ["air fryer", "air-fryer", "airfryer"],
    "sous_vide":                   ["sous vide", "sous-vide"],
    "smoker":                      ["smoked", "smoker", "low and slow"],
    "grill":                       ["grill", "grilled", "barbecue", "bbq"],
    "oven":                        ["bake", "baked", "roast", "roasted", "broil", "braise"],
    "stovetop":                    ["sauté", "saute", "pan-fry", "stir-fry", "sear", "simmer", "skillet"],
    "microwave":                   ["microwave"],
}

_PARSE_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?", re.IGNORECASE)


def _norm(s: str) -> str: return s.lower().strip()
def _contains(h: str, n: str) -> bool: return _norm(n) in _norm(h)


def _parse_minutes(raw: Optional[str]) -> Optional[int]:
    if not raw: return None
    m = _PARSE_DUR.match(raw)
    if not m: return None
    t = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return t if t > 0 else None


def _recipe_text(detail: dict) -> str:
    parts = [detail.get("name",""), detail.get("description","")]
    cuisine = detail.get("recipeCuisine")
    if isinstance(cuisine, str): parts.append(cuisine)
    for tag in detail.get("tags", []):
        parts.append(tag.get("name","") if isinstance(tag, dict) else str(tag))
    for cat in detail.get("recipeCategory", []):
        parts.append(cat.get("name","") if isinstance(cat, dict) else str(cat))
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict): continue
        parts.append(ing.get("note","") or "")
        food = ing.get("food")
        if food: parts.append(food.get("name","") if isinstance(food,dict) else str(food))
    return " ".join(p for p in parts if p)


def _ingredient_names(detail: dict) -> set[str]:
    names: set[str] = set()
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict): continue
        food = ing.get("food")
        if food:
            n = food.get("name","") if isinstance(food,dict) else str(food)
            if n: names.add(_norm(n))
        note = ing.get("note","")
        if note: names.add(_norm(note))
    return names


def _required_methods(detail: dict) -> set[str]:
    text = _recipe_text(detail).lower()
    return {m for m, kws in _METHOD_KEYWORDS.items() if any(k in text for k in kws)}


def _score_mealie(
    detail: dict,
    pantry_set: set[str],
    staples_set: set[str],
    prefs: Optional[models.Preference],
    weekly_hints: list[str],
) -> tuple[float, float, list[str]]:
    all_on_hand = pantry_set | staples_set
    ing_names   = _ingredient_names(detail)
    if ing_names:
        on_hand     = {n for n in ing_names if any(_contains(o,n) or _contains(n,o) for o in all_on_hand)}
        missing     = [n for n in ing_names if n not in on_hand]
        overlap_pct = len(on_hand) / len(ing_names)
    else:
        on_hand = set(); missing = []; overlap_pct = 0.0

    score = overlap_pct * _PANTRY_MAX_PTS
    text  = _recipe_text(detail)

    if prefs:
        for excl in (prefs.excluded_items or []):
            if _contains(text, excl): return _HARD_REJECT, overlap_pct, missing
        if prefs.available_methods:
            if _required_methods(detail) - set(prefs.available_methods):
                return _HARD_REJECT, overlap_pct, missing
        score -= min(sum(_DISLIKE_PTS_EACH for d in (prefs.disliked_items or []) if _contains(text, d)), _DISLIKE_MAX_PENALTY)
        score += min(sum(_LIKED_PTS_EACH  for li in (prefs.liked_items   or []) if _contains(text, li)), _LIKED_MAX_PTS)
        if prefs.max_cook_time_minutes:
            mins = _parse_minutes(detail.get("totalTime") or detail.get("performTime"))
            if mins and mins > prefs.max_cook_time_minutes:
                score -= _COOK_TIME_PENALTY

    score += min(sum(_WEEKLY_HINT_PTS_EACH for h in weekly_hints if _contains(text, h)), _WEEKLY_HINT_MAX_PTS)
    return score, overlap_pct, missing


def _build_suppressed_ids(
    household_id: str,
    current_week: date,
    db: Session,
) -> set[str]:
    excluded: set[str] = set()
    for r in db.query(models.RecipeRejection).filter(
        models.RecipeRejection.household_id == household_id
    ).all():
        if r.is_permanent:
            excluded.add(r.recipe_id)
        elif r.rejected_week and r.suppress_weeks:
            if current_week < r.rejected_week + timedelta(weeks=r.suppress_weeks):
                excluded.add(r.recipe_id)
    log.debug("Suppressed recipe IDs: %d", len(excluded))
    return excluded


# ── Public entry point ─────────────────────────────────────────────────────────

def build_suggestions(
    household_id: str,
    week_start: date,
    db: Session,
    num_override: Optional[int] = None,
) -> dict:
    """
    Builds the flat ranked suggestion list for the weekly planning session.

    Always returns up to n suggestions regardless of whether Mealie recipes
    are available or tagged.  Pool A (Mealie) is attempted first; Pool B
    (discovery) fills ALL remaining slots.
    """
    household = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not household:
        raise ValueError(f"Household {household_id!r} not found")

    prefs  = db.query(models.Preference).filter(models.Preference.household_id == household_id).first()
    intent = db.query(models.WeeklyIntent).filter(
        models.WeeklyIntent.household_id == household_id,
        models.WeeklyIntent.week_start_date == week_start,
    ).first()

    n = num_override or (intent.num_suggestions if intent and intent.num_suggestions else None) or \
        (prefs.default_num_suggestions if prefs else None) or 10

    weekly_hints = list(intent.ingredient_hints) if intent else []

    disc_cfg       = config_files.get_discovery_config()
    mealie_min_rat = int(disc_cfg.get("mealie_min_rating", prefs.favorite_rating_threshold if prefs else 4))
    mealie_fav_cnt = int(disc_cfg.get("mealie_favorites_count", 2))

    log.info(
        "=== build_suggestions | household=%s | week=%s | n=%d | "
        "hints=%s | mealie_min=%d | mealie_slots=%d ===",
        household_id, week_start, n, weekly_hints, mealie_min_rat, mealie_fav_cnt,
    )

    pantry_rows  = db.query(models.PantryItem).filter(models.PantryItem.household_id == household_id).all()
    pantry_set   = {_norm(p.name) for p in pantry_rows}
    staples_set  = {_norm(s) for s in config_files.get_staples()}
    excluded_ids = _build_suppressed_ids(household_id, week_start, db)
    local_by_slug= {r.mealie_slug: r for r in db.query(models.Recipe).filter(models.Recipe.mealie_slug.isnot(None)).all()}

    log.debug("Pantry items: %d, staples: %d", len(pantry_set), len(staples_set))

    suggestions: list[dict] = []
    mealie_ok = True

    # ── Pool A: Mealie proven favourites ──────────────────────────────────────
    dinner_tag = (prefs.mealie_dinner_tag if prefs else "") or ""
    mealie_favs: list[dict] = []

    try:
        top_rated = mealie_client.get_top_rated_recipes(
            min_rating=mealie_min_rat,
            per_page=30,
        )
        log.info("Mealie top-rated recipes retrieved: %d (min_rating=%d)", len(top_rated), mealie_min_rat)

        for summary in top_rated:
            slug = summary.get("slug")
            if not slug:
                continue
            local = local_by_slug.get(slug)
            if not local:
                # Create a minimal local row so this Mealie recipe gets a
                # stable recipe_id.  Without this, recipe_id would be None
                # for every Pool A recipe that was never confirmed through
                # the planner, causing all of them to share recipe_id=None
                # — meaning rejecting one visually "rejects" all of them.
                # Safety net: check the DB first in case a concurrent
                # request (or a repeated slug in top_rated) already
                # created the stub and flushed it in this session.
                _existing_stub = db.query(models.Recipe).filter(
                    models.Recipe.mealie_slug == slug
                ).first()
                if _existing_stub:
                    local = _existing_stub
                    log.debug("Pool A: found existing stub for slug=%s", slug)
                else:
                    local = models.Recipe(
                        source_url=f"mealie:{slug}",
                        title=slug,      # updated below once detail is fetched
                        mealie_slug=slug,
                    )
                    db.add(local)
                    try:
                        db.flush()
                    except Exception as _flush_err:
                        # Concurrent request beat us to it — roll back the
                        # pending add and fetch the row that won the race.
                        db.expunge(local)
                        local = db.query(models.Recipe).filter(
                            models.Recipe.mealie_slug == slug
                        ).first()
                        if not local:
                            log.warning("Pool A: flush failed and stub gone for slug=%s: %s",
                                        slug, _flush_err)
                            continue
                        log.debug("Pool A: recovered existing stub after flush race for slug=%s", slug)
                local_by_slug[slug] = local
                log.debug("Pool A: registered stub for slug=%s", slug)

            recipe_id = local.id
            if recipe_id in excluded_ids:
                log.debug("Pool A: skipping suppressed slug=%s", slug)
                continue
            try:
                detail = mealie_client.get_recipe(slug)
            except mealie_client.MealieError as e:
                log.warning("Pool A: could not fetch detail for slug=%s: %s", slug, e)
                continue

            # Sync real title back to stub row if we just created it
            real_title = detail.get("name", slug)
            if local.title == slug and real_title != slug:
                local.title = real_title
                db.add(local)

            score, overlap, missing = _score_mealie(detail, pantry_set, staples_set, prefs, weekly_hints)
            if score == _HARD_REJECT:
                log.debug("Pool A: HARD REJECT slug=%s", slug)
                continue

            score += _FAVORITE_BONUS  # Pool A base bonus

            # Soft dinner-tag boost (replaces previous hard filter)
            has_tag = mealie_client.recipe_has_tag(detail, dinner_tag) if dinner_tag else False
            if has_tag:
                score += _DINNER_TAG_BONUS
                log.debug("Pool A: dinner-tag boost +%d for '%s'", _DINNER_TAG_BONUS, detail.get("name", slug))
            elif dinner_tag:
                log.debug("Pool A: slug=%s lacks dinner tag '%s' — still included (soft filter)", slug, dinner_tag)

            mealie_favs.append({
                "recipe_id":           recipe_id,
                "title":               detail.get("name", slug),
                "mealie_slug":         slug,
                "source_url":          local.source_url if local else f"mealie:{slug}",
                "score":               round(score, 1),
                "pantry_overlap_pct":  round(overlap * 100, 1),
                "missing_ingredients": missing[:15],
                "is_favorite":         True,
                "total_time_minutes":  _parse_minutes(detail.get("totalTime") or detail.get("performTime")),
                "protein_category":    recipe_discovery._classify_protein(detail.get("name", slug), []),
                "_pending_import":     False,
            })

        mealie_favs.sort(key=lambda r: -r["score"])
        log.info(
            "Pool A: %d Mealie candidates scored, capping at %d",
            len(mealie_favs), mealie_fav_cnt,
        )
        mealie_favs = mealie_favs[:mealie_fav_cnt]

    except mealie_client.MealieError as e:
        log.warning("Mealie unavailable for Pool A: %s", e)
        mealie_ok = False

    suggestions.extend(mealie_favs)
    fav_slugs = {r["mealie_slug"] for r in mealie_favs if r["mealie_slug"]}

    # ── Pool B: Discovered recipes — fill ALL remaining slots ─────────────────
    # Ask discovery for more than we need so dedup doesn't leave us short
    discovery_need = n - len(mealie_favs)
    discovery_ask  = max(discovery_need + 10, 15)   # always ask for at least 15

    log.info(
        "Pool B: need %d more suggestions → asking discovery for %d",
        discovery_need, discovery_ask,
    )

    discovered = recipe_discovery.discover_and_score(
        household_id=household_id,
        week_start=week_start,
        db=db,
        pantry_set=pantry_set,
        staples_set=staples_set,
        prefs=prefs,
        weekly_hints=weekly_hints,
        excluded_recipe_ids=excluded_ids,
        max_results=discovery_ask,
    )

    log.info("Pool B: discovery returned %d results", len(discovered))

    pool_b_added = 0
    for r in discovered:
        if len(suggestions) >= n:
            break
        if r.get("mealie_slug") in fav_slugs:
            log.debug("Pool B: dedup skip mealie_slug=%s", r.get("mealie_slug"))
            continue
        suggestions.append(r)
        pool_b_added += 1

    # ── Final sort + rank ──────────────────────────────────────────────────────
    # Mealie favourites (is_favorite=True) sort BELOW discovered recipes so
    # new Pool B suggestions surface at the top. Within each group recipes
    # are still ordered by descending score.
    suggestions.sort(key=lambda r: (1 if r.get("is_favorite") else 0, -r["score"]))
    suggestions = suggestions[:n]

    log.info(
        "=== Suggestions built | total=%d | pool_a=%d | pool_b=%d | "
        "top_score=%.1f ===",
        len(suggestions), len(mealie_favs), pool_b_added,
        suggestions[0]["score"] if suggestions else 0,
    )

    return {
        "week_start_date":        week_start.isoformat(),
        "household_id":           household_id,
        "num_suggestions":        n,
        "mealie_available":       mealie_ok,
        "dinner_tag_filter":      dinner_tag,
        "weekly_hints_applied":   weekly_hints,
        "mealie_favorites_shown": len(mealie_favs),
        "discoveries_shown":      pool_b_added,
        "suggestions": [{"rank": i + 1, **r} for i, r in enumerate(suggestions)],
    }
