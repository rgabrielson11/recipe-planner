import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, config_files, mealie_client
from app.database import get_db
log = logging.getLogger(__name__)
from app.url_safety import UnsafeUrlError, assert_safe_url

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/rejection-reasons")
def get_rejection_reasons():
    """
    Returns the full rejection reason vocabulary with permanence flags.

    permanent: true  → use for genuine dislikes, allergies, missing equipment.
                        The recipe will never be suggested to this household again.

    permanent: false → use for situational passes (not this week, made recently,
                        too expensive right now). The recipe resurfaces after
                        suppress_weeks weeks so the catalog keeps growing.
    """
    return {"reasons": config_files.get_rejection_reasons()}


@router.post("/import", response_model=schemas.RecipeOut)
def import_recipe(payload: schemas.RecipeImport, db: Session = Depends(get_db)):
    """
    Imports a recipe from a URL into Mealie (Mealie does the actual scraping),
    then stores a local reference row. Idempotent on source_url.
    """
    existing = db.query(models.Recipe).filter(
        models.Recipe.source_url == payload.source_url
    ).first()
    if existing:
        return existing

    try:
        assert_safe_url(payload.source_url)
    except UnsafeUrlError as e:
        # Mealie fetches this URL server-side on our behalf — Mealie lives on
        # the same trusted LAN, so an unvalidated URL here is an SSRF vector
        # against internal services. Reject before it ever reaches Mealie.
        raise HTTPException(status_code=422, detail=f"Invalid recipe URL: {e}")

    try:
        slug         = mealie_client.import_recipe_from_url(payload.source_url)
        mealie_recipe= mealie_client.get_recipe(slug)
        title        = mealie_recipe.get("name", payload.source_url)
    except mealie_client.MealieError as e:
        raise HTTPException(status_code=502, detail=f"Mealie import failed: {e}")

    recipe = models.Recipe(source_url=payload.source_url, title=title, mealie_slug=slug)
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


@router.post("", response_model=schemas.RecipeOut)
def create_recipe(payload: schemas.RecipeCreate, db: Session = Depends(get_db)):
    """
    Manual recipe stub (no Mealie sync). Useful for testing or for family
    recipes you don't want in Mealie. Idempotent on source_url.
    """
    existing = db.query(models.Recipe).filter(
        models.Recipe.source_url == payload.source_url
    ).first()
    if existing:
        return existing
    recipe = models.Recipe(**payload.model_dump())
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


@router.get("", response_model=list[schemas.RecipeOut])
def list_recipes(db: Session = Depends(get_db)):
    return db.query(models.Recipe).all()


@router.post("/{recipe_id}/reject", response_model=schemas.RejectionOut)
def reject_recipe(
    recipe_id: str,
    payload: schemas.RejectionCreate,
    db: Session = Depends(get_db),
):
    """
    Records why the household passed on a suggested recipe.

    Permanent reasons (dislike, allergy, cook_method_unavailable,
    cookware_unavailable, disliked_ingredient): the recipe will NEVER be
    suggested to this household again.

    Temporary reasons (not_this_week, already_made_recently,
    too_time_consuming, too_expensive, too_complex, missing_key_ingredient,
    other): the recipe is suppressed for the configured number of weeks
    (see GET /recipes/rejection-reasons for suppress_weeks per reason),
    then resurfaces in future suggestion pools. The catalog keeps growing.

    rejected_week defaults to the current Monday-anchored week if omitted.
    """
    recipe = db.query(models.Recipe).filter(models.Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    reason_def = config_files.get_rejection_reason(payload.reason_category)
    if reason_def is None:
        valid_keys = [r["key"] for r in config_files.get_rejection_reasons()]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown reason_category. Valid values: {valid_keys}",
        )

    is_permanent   = reason_def["permanent"]
    suppress_weeks = reason_def.get("suppress_weeks") if not is_permanent else None
    rejected_week  = payload.rejected_week or date.today()

    rejection = models.RecipeRejection(
        household_id=payload.household_id,
        recipe_id=recipe_id,
        reason_category=payload.reason_category,
        reason_detail=payload.reason_detail,
        is_permanent=is_permanent,
        rejected_week=rejected_week,
        suppress_weeks=suppress_weeks,
    )
    db.add(rejection)
    db.commit()
    db.refresh(rejection)
    return rejection


@router.get("/{recipe_id}/rejections", response_model=list[schemas.RejectionOut])
def list_rejections(recipe_id: str, db: Session = Depends(get_db)):
    """All rejection records for a specific recipe."""
    return db.query(models.RecipeRejection).filter(
        models.RecipeRejection.recipe_id == recipe_id
    ).all()


@router.get("/{recipe_id}/print-data")
def get_print_data(recipe_id: str, db: Session = Depends(get_db)):
    """
    Returns all available recipe data needed for the in-app print view.
    Combines local DB fields (scraped ingredients, description, cook time)
    with full instructions from Mealie when the recipe has been imported there.
    """
    import json as _json
    import os as _os

    recipe = db.query(models.Recipe).filter(models.Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # ── Local data (always available after scraping) ──────────────────────────
    try:
        ingredients = _json.loads(recipe.scraped_ingredients_json or "[]")
    except Exception:
        ingredients = []

    try:
        instructions = _json.loads(recipe.scraped_instructions_json or "[]")
    except Exception:
        instructions = []

    data = {
        "title":               recipe.title,
        "source_url":          recipe.source_url,
        "total_time_minutes":  recipe.scraped_time_minutes,
        "servings":            recipe.scraped_servings or "",
        "description":         recipe.scraped_description or "",
        "ingredients":         ingredients,
        "instructions":        instructions,  # from scrape; overridden by Mealie if richer
        "mealie_url":          None,
    }

    # ── Mealie data (instructions, notes) — best-effort ──────────────────────
    if recipe.mealie_slug:
        mealie_base = _os.getenv("MEALIE_BASE_URL", "").rstrip("/")
        if mealie_base:
            data["mealie_url"] = f"{mealie_base}/g/home/r/{recipe.mealie_slug}"
        try:
            detail = mealie_client.get_recipe(recipe.mealie_slug)
            # Prefer Mealie's ingredient list (has quantities + units) if richer
            mealie_ings = [
                " ".join(filter(None, [
                    str(i.get("quantity", "") or "").strip(),
                    (i.get("unit") or {}).get("name", ""),
                    (i.get("food")  or {}).get("name", "") or i.get("note", ""),
                ])).strip()
                for i in (detail.get("recipeIngredient") or [])
                if isinstance(i, dict)
            ]
            if mealie_ings:
                data["ingredients"] = [i for i in mealie_ings if i]
            mealie_servings = str(detail.get("recipeServings") or detail.get("recipeYield") or "").strip()
            if mealie_servings:
                data["servings"] = mealie_servings

            # Instructions
            data["instructions"] = [
                step.get("text", "")
                for step in (detail.get("recipeInstructions") or [])
                if isinstance(step, dict) and step.get("text", "").strip()
            ]
        except Exception:
            pass   # Mealie unavailable — fall back to scraped data only

    return data


@router.delete("/{recipe_id}/reject")
def unblock_recipe(recipe_id: str, household_id: str, db: Session = Depends(get_db)):
    """
    Remove all permanent rejections for a recipe+household combination,
    effectively unblocking it so it can appear in suggestions again.
    """
    deleted = (
        db.query(models.RecipeRejection)
        .filter(
            models.RecipeRejection.recipe_id == recipe_id,
            models.RecipeRejection.household_id == household_id,
            models.RecipeRejection.is_permanent == True,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    log.info("Unblocked recipe %s for household %s (%d rejection(s) removed)", recipe_id, household_id, deleted)
    return {"unblocked": recipe_id, "removed": deleted}


@router.get("/search")
def search_recipes(
    q: str,
    household_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Full-text search across all scraped recipe stubs by title.
    Returns recipes ranked by relevance (exact word match first, then partial).
    Includes rejection and rating status for the household.
    """
    q_lower = q.strip().lower()
    if not q_lower:
        return {"results": [], "total": 0}

    # Fetch all recipes with title matching the query
    from sqlalchemy import func
    recipes = (
        db.query(models.Recipe)
        .filter(func.lower(models.Recipe.title).contains(q_lower))
        .order_by(models.Recipe.title)
        .limit(limit * 3)   # over-fetch to allow relevance re-ranking
        .all()
    )

    # Lookup rejection and rating status for this household
    blocked_ids = {
        r.recipe_id for r in db.query(models.RecipeRejection).filter(
            models.RecipeRejection.household_id == household_id,
            models.RecipeRejection.is_permanent == True,
        ).all()
    }
    rated_map = {}
    for entry in db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == household_id,
        models.MealPlanEntry.rating.isnot(None),
    ).all():
        if entry.recipe_id not in rated_map:
            rated_map[entry.recipe_id] = entry.rating

    from app import recipe_discovery as _rd

    results = []
    for r in recipes:
        # Relevance: exact word match scores higher
        import re as _re
        title_lower = r.title.lower()
        exact = bool(_re.search(r"\b" + _re.escape(q_lower) + r"\b", title_lower))
        results.append({
            "recipe_id":          r.id,
            "title":              r.title,
            "source_url":         r.source_url,
            "mealie_slug":        r.mealie_slug,
            "total_time_minutes": r.scraped_time_minutes,
            "carbs_per_serving":  r.scraped_carbs,
            "scraped_servings":   r.scraped_servings,
            "protein_category":   _rd._classify_protein(r.title, []),
            "is_blocked":         r.id in blocked_ids,
            "rating":             rated_map.get(r.id),
            "missing_ingredients": [],
            "pantry_overlap_pct":  0,
            "_exact":             exact,
        })

    # Compute missing ingredients vs household pantry + staples
    from app import config_files as _cf
    try:
        staples_set = set(s.lower() for s in _cf.get_staples())
    except Exception:
        staples_set = set()
    pantry_rows = db.query(models.PantryItem).filter(
        models.PantryItem.household_id == household_id
    ).all()
    pantry_set = set(p.name.lower() for p in pantry_rows) | staples_set

    stub_map = {r.id: r for r in db.query(models.Recipe).filter(
        models.Recipe.id.in_([x["recipe_id"] for x in results])
    ).all()}
    import json as _j
    for res in results:
        stub = stub_map.get(res["recipe_id"])
        if stub and stub.scraped_tokens_json:
            try:
                tokens = set(_j.loads(stub.scraped_tokens_json))
                missing = sorted(t for t in tokens if t not in pantry_set)
                overlap = len(tokens & pantry_set) / len(tokens) if tokens else 0
                res["missing_ingredients"] = missing[:15]
                res["pantry_overlap_pct"] = round(overlap * 100, 1)
            except Exception:
                pass

    # Sort: exact matches first, then alphabetical
    results.sort(key=lambda x: (0 if x["_exact"] else 1, x["title"]))
    for r in results:
        del r["_exact"]

    return {"results": results[:limit], "total": len(results)}
