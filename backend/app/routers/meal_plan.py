"""
Meal plan router — selections, suggestions, shopping list, ratings.

Weekly workflow:
  1. GET  /meal-plan/pantry-review/{household_id}
  2. POST /meal-plan/week-intent/{household_id}/{week_start_date}
  3. GET  /meal-plan/suggest
  4. (Optional) POST /recipes/{id}/reject for skipped recipes
  5. POST /meal-plan/selections   ← locks in chosen recipes
  6. GET  /meal-plan/shopping-list
  7. (End of week) POST /meal-plan/entries/{id}/review
"""

from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import logging
from app import models, schemas, mealie_client, matching_engine, recipe_discovery, shopping_list as sl, bring_client

log = logging.getLogger(__name__)
from app.database import get_db

router = APIRouter(prefix="/meal-plan", tags=["meal-plan"])


# ── 1. Pantry review snapshot ─────────────────────────────────────────────────

@router.get("/pantry-review/{household_id}")
def pantry_weekly_review(household_id: str, db: Session = Depends(get_db)):
    """
    Categorized snapshot of the household's current pantry and staples.
    Use this at the start of each weekly planning session to review what's
    on hand, flag expired items, and decide what to update before generating
    suggestions.

    Pantry updates use the existing PATCH /pantry/{item_id} and POST /pantry
    endpoints. This endpoint is read-only.
    """
    from app import config_files

    household = db.query(models.Household).filter(
        models.Household.id == household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    items = db.query(models.PantryItem).filter(
        models.PantryItem.household_id == household_id
    ).order_by(models.PantryItem.category, models.PantryItem.name).all()

    today       = date.today()
    by_category: dict[str, list[dict]] = defaultdict(list)

    for item in items:
        expired = item.expiry_date is not None and item.expiry_date < today
        by_category[item.category or "uncategorized"].append({
            "id":          item.id,
            "name":        item.name,
            "quantity":    float(item.quantity) if item.quantity is not None else None,
            "unit":        item.unit,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
            "expired":     expired,
        })

    staples = config_files.get_staples()

    return {
        "household_id":        household_id,
        "as_of_date":          today.isoformat(),
        "pantry_by_category":  dict(by_category),
        "total_tracked_items": len(items),
        "expired_items": [
            i["name"]
            for group in by_category.values()
            for i in group
            if i["expired"]
        ],
        "staples":      staples,
        "staples_note": (
            "Staples are always assumed on hand and never added to shopping lists. "
            "Edit backend/app/data/pantry_staples.yaml or use POST/DELETE /pantry/staples."
        ),
        "next_steps": [
            "Update expired or depleted items via PATCH /pantry/{item_id}",
            "Add new on-hand items via POST /pantry",
            "Then record this week's intent via POST /meal-plan/week-intent/{household_id}/{week_start_date}",
        ],
    }


# ── 2. Weekly intent ──────────────────────────────────────────────────────────

@router.post(
    "/week-intent/{household_id}/{week_start_date}",
    response_model=schemas.WeeklyIntentOut,
)
def set_week_intent(
    household_id: str,
    week_start_date: date,
    payload: schemas.WeeklyIntentCreate,
    db: Session = Depends(get_db),
):
    """
    Records the household's intent for this week — what they want to feature
    and how many suggestions to generate.

    ingredient_hints: free-text keywords (proteins, cuisines, themes) that
    boost matching recipe scores by +15 pts each (max +45) for this week only.
    Stronger than the permanent liked_items signal (+5 each) so the week's
    theme dominates. e.g. ["chicken thighs", "salmon", "bbq", "quick weeknight"]

    num_suggestions: how many recipes to pull in the flat suggestion list.
    Varies week to week — some weeks you want 5 options, some 15.
    Omit to use the household's default_num_suggestions preference (default 10).

    Calling this again replaces the existing intent for that week.
    """
    household = db.query(models.Household).filter(
        models.Household.id == household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    existing = db.query(models.WeeklyIntent).filter(
        models.WeeklyIntent.household_id == household_id,
        models.WeeklyIntent.week_start_date == week_start_date,
    ).first()

    if existing:
        if payload.ingredient_hints is not None:
            existing.ingredient_hints = payload.ingredient_hints
        if payload.num_suggestions is not None:
            existing.num_suggestions = payload.num_suggestions
        if payload.pantry_snapshot_notes is not None:
            existing.pantry_snapshot_notes = payload.pantry_snapshot_notes
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    intent = models.WeeklyIntent(
        household_id=household_id,
        week_start_date=week_start_date,
        **payload.model_dump(),
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


@router.get(
    "/week-intent/{household_id}/{week_start_date}",
    response_model=schemas.WeeklyIntentOut,
)
def get_week_intent(
    household_id: str,
    week_start_date: date,
    db: Session = Depends(get_db),
):
    """Returns the recorded weekly intent for the given household and week."""
    intent = db.query(models.WeeklyIntent).filter(
        models.WeeklyIntent.household_id == household_id,
        models.WeeklyIntent.week_start_date == week_start_date,
    ).first()
    if not intent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No weekly intent found for week {week_start_date}. "
                "Create one with POST /meal-plan/week-intent/{household_id}/{week_start_date}"
            ),
        )
    return intent


# ── 3. Suggestions (flat ranked list) ────────────────────────────────────────

@router.get("/suggest/progress")
def suggest_progress(household_id: str):
    """Poll while GET /suggest is in-flight — returns {pct, message}."""
    return recipe_discovery.get_progress(household_id)


@router.get("/suggest", response_model=schemas.WeeklySuggestion)
def suggest(
    household_id: str,
    week_start_date: date,
    num: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Override the number of suggestions for this request only. "
            "Priority: this param > WeeklyIntent.num_suggestions > "
            "Preference.default_num_suggestions > 10."
        ),
    ),
    db: Session = Depends(get_db),
):
    """
    Runs the matching engine and returns a flat ranked list of dinner
    recipe suggestions for the household to review and choose from.

    The engine automatically picks up this week's WeeklyIntent (hints +
    num_suggestions) if one has been recorded — no extra parameters needed.

    After reviewing the list:
      • Choose your recipes → POST /meal-plan/selections
      • Reject skipped recipes (optional) → POST /recipes/{id}/reject
        Use permanent reasons (dislike, allergy, cookware) to remove a recipe
        forever. Use temporary reasons (not_this_week, already_made_recently)
        to suppress it for a few weeks and let it resurface later.
    """
    if recipe_discovery.is_scraping():
        raise HTTPException(
            status_code=503,
            detail="Recipe scraping is in progress — suggestions will be available once the scrape completes. Check the Sources page for progress.",
        )

    try:
        result = matching_engine.build_suggestions(
            household_id=household_id,
            week_start=week_start_date,
            db=db,
            num_override=num,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


# ── 5. Confirm selections ─────────────────────────────────────────────────────

@router.post("/selections", response_model=schemas.WeeklySelectionSummary)
def confirm_selections(
    payload: schemas.WeeklySelectionCreate,
    db: Session = Depends(get_db),
):
    """
    Locks in the household's chosen recipes for the week.

    Replaces any prior selections for that week. Also creates a MealPlanEntry
    row for each selected recipe so the end-of-week rating flow works unchanged.

    The shopping list (GET /meal-plan/shopping-list) generates from ONLY these
    selections — not from the full suggestion pool.

    For recipes you're passing on, optionally call POST /recipes/{id}/reject
    with a reason category so the matching engine learns your preferences over
    time. Temporary reasons suppress for a few weeks; permanent reasons remove
    the recipe from future suggestions entirely.
    """
    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    # Validate all recipe IDs exist
    recipes = db.query(models.Recipe).filter(
        models.Recipe.id.in_(payload.recipe_ids)
    ).all()
    found_ids = {r.id for r in recipes}
    missing   = [rid for rid in payload.recipe_ids if rid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Recipe IDs not found: {missing}",
        )

    # Clear prior selections for this week
    db.query(models.WeeklySelection).filter(
        models.WeeklySelection.household_id == payload.household_id,
        models.WeeklySelection.week_start_date == payload.week_start_date,
    ).delete()

    # Clear any existing meal plan entries created by a prior selection pass
    db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == payload.household_id,
        models.MealPlanEntry.week_start_date == payload.week_start_date,
        models.MealPlanEntry.rating.is_(None),  # only un-reviewed entries
    ).delete()

    db.commit()

    # Create new selections + meal plan entries
    new_selections:  list[models.WeeklySelection] = []
    entry_ids:       list[str]                    = []

    for recipe_id in payload.recipe_ids:
        sel = models.WeeklySelection(
            household_id=payload.household_id,
            week_start_date=payload.week_start_date,
            recipe_id=recipe_id,
        )
        db.add(sel)
        new_selections.append(sel)

        entry = models.MealPlanEntry(
            household_id=payload.household_id,
            recipe_id=recipe_id,
            week_start_date=payload.week_start_date,
        )
        db.add(entry)
        db.flush()  # get the generated ID
        entry_ids.append(entry.id)

    db.commit()

    # ── Auto-import newly discovered recipes into Mealie ─────────────────
    # Any selected recipe that has no mealie_slug yet was discovered via
    # recipe-scrapers and has never been in Mealie. Import it now and apply
    # the dinner-planner tag so it appears in future Mealie-based suggestions.
    prefs = db.query(models.Preference).filter(
        models.Preference.household_id == payload.household_id
    ).first()
    dinner_tag = (prefs.mealie_dinner_tag if prefs else "") or "dinner-planner"
    import_results: list[dict] = []

    for recipe in recipes:
        if recipe.mealie_slug:
            log.info("Selection '%s' already in Mealie (slug=%s)", recipe.title, recipe.mealie_slug)
            import_results.append({"title": recipe.title, "status": "already_in_mealie", "slug": recipe.mealie_slug})
            continue
        try:
            log.info("Importing '%s' from %s", recipe.title, recipe.source_url)

            # ── Dedup check: recipe may already exist in Mealie ───────────
            # Handles DB-reset scenarios where our local slug was lost but
            # the recipe still lives in Mealie. Avoids creating duplicates.
            existing_slug = mealie_client.find_recipe_by_url(recipe.source_url)
            if existing_slug:
                log.info("Recipe already in Mealie (orgURL match): '%s' slug=%s", recipe.title, existing_slug)
                recipe.mealie_slug = existing_slug
                db.add(recipe)
                db.commit()
                import_results.append({"title": recipe.title, "status": "already_in_mealie", "slug": existing_slug})
                continue

            slug = mealie_client.import_recipe_from_url(recipe.source_url)

            # ── Commit slug IMMEDIATELY, independently of tagging ──────────
            # Tag failures must NEVER prevent the slug from being saved.
            # The shopping list depends on this slug to fetch ingredients.
            recipe.mealie_slug = slug
            db.add(recipe)
            db.commit()
            log.info("Slug saved to DB: '%s' → %s", recipe.title, slug)

            # Tag is best-effort — failure is logged but does not raise
            try:
                mealie_client.add_tag_to_recipe(slug, dinner_tag)
                log.info("Tagged '%s' with '%s'", slug, dinner_tag)
            except mealie_client.MealieError as tag_err:
                log.warning("Tag failed for '%s' (slug=%s): %s — slug already saved, continuing", recipe.title, slug, tag_err)

            import_results.append({"title": recipe.title, "status": "imported", "slug": slug})

        except mealie_client.MealieError as e:
            log.warning("Mealie import FAILED for '%s': %s", recipe.title, e)
            import_results.append({
                "title":      recipe.title,
                "status":     "import_failed",
                "error":      str(e),
                "source_url": recipe.source_url,   # needed for manual-import deep link in UI
            })

    db.commit()   # final commit for any remaining state

    return {
        "week_start_date":     payload.week_start_date,
        "household_id":        payload.household_id,
        "selected_recipes":    recipes,
        "meal_plan_entry_ids": entry_ids,
        "mealie_imports":      import_results,
    }


@router.get("/selections", response_model=list[schemas.WeeklySelectionOut])
def get_selections(
    household_id: str,
    week_start_date: date,
    db: Session = Depends(get_db),
):
    """Returns the confirmed recipe selections for the given week."""
    return db.query(models.WeeklySelection).filter(
        models.WeeklySelection.household_id == household_id,
        models.WeeklySelection.week_start_date == week_start_date,
    ).all()


# ── 6. Shopping list ──────────────────────────────────────────────────────────

@router.get("/shopping-list", response_model=schemas.ShoppingList)
def get_shopping_list(
    household_id: str,
    week_start_date: date,
    db: Session = Depends(get_db),
):
    """
    Generates the shopping list for the week from confirmed selections only.

    Pipeline:
      1. Fetch ingredient details for each selected recipe from Mealie
      2. Scale all quantities to household size (recipe yield → num_people)
      3. Aggregate totals across all recipes (sum first, round once)
      4. Subtract pantry on-hand quantities (same unit) and staples
      5. Round remainder UP to nearest real package size (package_sizes.yaml)
      6. Group by store section

    Requires at least one confirmed selection (POST /meal-plan/selections).
    Any recipes without Mealie slugs or unreachable Mealie entries appear
    in the warnings field and are excluded from the ingredient calculation.
    """
    try:
        result = sl.build_shopping_list(
            household_id=household_id,
            week_start=week_start_date,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── 6b. Push shopping list to Bring! (Patch 16) ────────────────────────────────

@router.get("/bring/lists")
async def get_bring_lists():
    """
    Lists the Bring! account's shopping lists (name + uuid) — used by the
    Settings UI so you can pick which one to push to by exact name. Requires
    BRING_EMAIL / BRING_PASSWORD to be set in .env.
    """
    try:
        return {"lists": await bring_client.list_bring_lists()}
    except bring_client.BringError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/shopping-list/push-to-bring")
async def push_shopping_list_to_bring(
    household_id: str,
    week_start_date: date,
    db: Session = Depends(get_db),
):
    """
    Generates the shopping list the same way GET /shopping-list does, then
    pushes every BUY item (not pantry_check, not using_from_pantry — those
    are already on hand) to the household's configured Bring! list
    (Preference.bring_list_name), so it shows up natively in the Bring!
    app instead of only in this UI.

    Pushing the same week twice is safe — Bring!'s add-item call updates
    the existing item's quantity in place rather than duplicating it.
    """
    try:
        result = sl.build_shopping_list(
            household_id=household_id,
            week_start=week_start_date,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    prefs = db.query(models.Preference).filter(
        models.Preference.household_id == household_id
    ).first()
    list_name = prefs.bring_list_name if prefs else None

    try:
        push_result = await bring_client.push_shopping_list(result, list_name=list_name)
    except bring_client.BringError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "Pushed shopping list to Bring!: household=%s week=%s list=%r pushed=%d errors=%d",
        household_id, week_start_date, push_result.get("list_name"),
        len(push_result.get("pushed", [])), len(push_result.get("errors", [])),
    )
    return push_result


# ── 7. End-of-week review ─────────────────────────────────────────────────────

@router.get("/entries", response_model=list[schemas.MealPlanEntryOut])
def list_entries(
    household_id: str,
    week_start_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == household_id
    )
    if week_start_date:
        query = query.filter(models.MealPlanEntry.week_start_date == week_start_date)
    return query.order_by(models.MealPlanEntry.week_start_date.desc()).all()


@router.post("/entries/{entry_id}/review", response_model=schemas.MealPlanEntryOut)
def review_entry(
    entry_id: str,
    payload: schemas.MealPlanEntryReview,
    db: Session = Depends(get_db),
):
    """
    Records a 1–5 star rating for a recipe cooked this week.

    If the rating meets or exceeds the household's favorite_rating_threshold
    (default 4 stars), the recipe is marked a favorite and synced to Mealie.
    The matching engine boosts favorites in future suggestion pools (+25 pts).
    """
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")

    entry = db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.id == entry_id
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")

    prefs     = db.query(models.Preference).filter(
        models.Preference.household_id == entry.household_id
    ).first()
    threshold = prefs.favorite_rating_threshold if prefs else 4

    entry.rating      = payload.rating
    entry.is_favorite = payload.rating >= threshold
    entry.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)

    if entry.is_favorite:
        recipe = db.query(models.Recipe).filter(
            models.Recipe.id == entry.recipe_id
        ).first()
        if recipe and recipe.mealie_slug:
            try:
                mealie_client.set_recipe_rating(recipe.mealie_slug, payload.rating)
            except mealie_client.MealieError:
                pass   # local rating saved; Mealie sync is best-effort

    return entry


@router.get("/favorites", response_model=list[schemas.RecipeOut])
def list_favorites(household_id: str, db: Session = Depends(get_db)):
    """All recipes this household has rated at or above their favorite threshold."""
    recipe_ids = (
        db.query(models.MealPlanEntry.recipe_id)
        .filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.is_favorite.is_(True),
        )
        .distinct()
    )
    return db.query(models.Recipe).filter(models.Recipe.id.in_(recipe_ids)).all()


@router.get("/weekly-review", response_model=list[schemas.MealPlanEntryOut])
def weekly_review(
    household_id: str,
    week_start_date: date,
    db: Session = Depends(get_db),
):
    """Returns the week's meal plan entries with current review status."""
    return db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == household_id,
        models.MealPlanEntry.week_start_date == week_start_date,
    ).all()


# ── Pending feedback ──────────────────────────────────────────────────────────

@router.get("/pending-feedback")
def get_pending_feedback(household_id: str, week_start_date: str, db: Session = Depends(get_db)):
    """
    Return ALL past meals that haven't been rated or permanently blocked,
    sorted most-recent-week first. Excludes:
      - Entries with a rating already set
      - Entries whose recipe has a permanent rejection for this household
    week_start_date is used only to exclude the current planning week itself.
    """
    from datetime import date as _date
    try:
        current = _date.fromisoformat(week_start_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid week_start_date")

    # Recipe IDs that have been permanently blocked for this household
    blocked_ids = {
        r.recipe_id for r in db.query(models.RecipeRejection).filter(
            models.RecipeRejection.household_id == household_id,
            models.RecipeRejection.is_permanent == True,
        ).all()
    }

    # Recipe IDs that have been rated at least once — exclude entirely so that
    # rating via the Past Meals page removes the recipe from this list even if
    # the same recipe appeared in multiple weeks.
    rated_ids = {
        e.recipe_id for e in db.query(models.MealPlanEntry).filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.rating.isnot(None),
        ).all()
    }

    processed_ids = blocked_ids | rated_ids

    entries = (
        db.query(models.MealPlanEntry)
        .join(models.Recipe)
        .filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.week_start_date < current,
            models.MealPlanEntry.recipe_id.notin_(processed_ids) if processed_ids else True,
        )
        .order_by(models.MealPlanEntry.week_start_date.desc())
        .all()
    )

    result = []
    seen = set()
    for e in entries:
        if e.recipe_id in seen:   # dedup — show each recipe once
            continue
        seen.add(e.recipe_id)
        r = e.recipe
        result.append({
            "entry_id":       e.id,
            "recipe_id":      e.recipe_id,
            "title":          r.title if r else "Unknown",
            "source_url":     r.source_url if r else None,
            "mealie_slug":    r.mealie_slug if r else None,
            "week_start_date": str(e.week_start_date),
            "rating":         e.rating,
            "reviewed_at":    e.reviewed_at.isoformat() if e.reviewed_at else None,
        })
    return result


# ── Meal history ──────────────────────────────────────────────────────────────

@router.get("/history")
def get_meal_history(household_id: str, db: Session = Depends(get_db)):
    """
    Return past weekly selections grouped by week, most recent first.
    Each entry includes recipe title, source URL, and mealie slug so the
    frontend can display a link and offer re-selection.
    """
    rows = (
        db.query(models.WeeklySelection)
        .join(models.Recipe)
        .filter(models.WeeklySelection.household_id == household_id)
        .order_by(models.WeeklySelection.week_start_date.desc())
        .all()
    )

    # Group by week
    from collections import defaultdict
    weeks: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple] = set()   # (week, recipe_id) dedup

    for sel in rows:
        key = (str(sel.week_start_date), sel.recipe_id)
        if key in seen:
            continue
        seen.add(key)
        recipe = sel.recipe
        # Find the MealPlanEntry for this selection (for rating).
        # Try exact week match first; fall back to any entry for the recipe.
        entry = db.query(models.MealPlanEntry).filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.recipe_id == sel.recipe_id,
            models.MealPlanEntry.week_start_date == sel.week_start_date,
        ).first()
        if not entry:
            entry = db.query(models.MealPlanEntry).filter(
                models.MealPlanEntry.household_id == household_id,
                models.MealPlanEntry.recipe_id == sel.recipe_id,
            ).order_by(models.MealPlanEntry.created_at.desc()).first()
        # Check if permanently blocked
        is_blocked = db.query(models.RecipeRejection).filter(
            models.RecipeRejection.household_id == household_id,
            models.RecipeRejection.recipe_id == sel.recipe_id,
            models.RecipeRejection.is_permanent == True,
        ).first() is not None
        weeks[str(sel.week_start_date)].append({
            "recipe_id":          recipe.id,
            "entry_id":           entry.id if entry else None,
            "title":              recipe.title,
            "source_url":         recipe.source_url,
            "mealie_slug":        recipe.mealie_slug,
            "total_time_minutes": recipe.scraped_time_minutes,
            "rating":             entry.rating if entry else None,
            "is_blocked":         is_blocked,
        })

    return [
        {"week_start_date": week, "recipes": recipes}
        for week, recipes in weeks.items()
    ]


@router.post("/history/add")
def add_from_history(payload: schemas.WeeklySelectionCreate, db: Session = Depends(get_db)):
    """
    Add previously-selected recipes directly to the current week's plan,
    importing them to Mealie if not already imported.
    Returns import results in the same shape as confirm-selections.
    """
    return confirm_selections(payload, db)
