from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, mealie_client
from app.database import get_db

router = APIRouter(prefix="/meal-plan", tags=["meal-plan"])


@router.post("/entries", response_model=schemas.MealPlanEntryOut)
def add_entry(payload: schemas.MealPlanEntryCreate, db: Session = Depends(get_db)):
    """Slots a recipe into a household's plan for a given week."""
    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    recipe = db.query(models.Recipe).filter(models.Recipe.id == payload.recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    entry = models.MealPlanEntry(**payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/entries", response_model=list[schemas.MealPlanEntryOut])
def list_entries(household_id: str, week_start_date: date | None = None, db: Session = Depends(get_db)):
    query = db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == household_id
    )
    if week_start_date:
        query = query.filter(models.MealPlanEntry.week_start_date == week_start_date)
    return query.order_by(models.MealPlanEntry.week_start_date.desc()).all()


@router.post("/entries/{entry_id}/review", response_model=schemas.MealPlanEntryOut)
def review_entry(entry_id: str, payload: schemas.MealPlanEntryReview, db: Session = Depends(get_db)):
    """
    Records a 1-5 star rating for a meal that was actually cooked. If the
    rating meets or exceeds the household's favorite_rating_threshold
    (default 4), the entry — and the underlying recipe — is marked a
    favorite. The favorite status is also synced back to Mealie as a
    best-effort call; a Mealie failure here doesn't block the local rating
    from being saved.
    """
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")

    entry = db.query(models.MealPlanEntry).filter(models.MealPlanEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")

    prefs = db.query(models.Preference).filter(
        models.Preference.household_id == entry.household_id
    ).first()
    threshold = prefs.favorite_rating_threshold if prefs else 4

    entry.rating = payload.rating
    entry.is_favorite = payload.rating >= threshold
    entry.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)

    if entry.is_favorite:
        recipe = db.query(models.Recipe).filter(models.Recipe.id == entry.recipe_id).first()
        if recipe and recipe.mealie_slug:
            try:
                mealie_client.set_recipe_rating(recipe.mealie_slug, payload.rating)
            except mealie_client.MealieError:
                # Local rating is already saved; Mealie sync is best-effort.
                pass

    return entry


@router.get("/weekly-review", response_model=list[schemas.MealPlanEntryOut])
def weekly_review(household_id: str, week_start_date: date, db: Session = Depends(get_db)):
    """
    Returns the prior week's meal plan entries with their review status —
    the starting point for the weekly process: review last week's
    feedback, see which recipes became favorites, then build next week's
    plan as a mix of those favorites plus new candidates (Phase 3).
    """
    return db.query(models.MealPlanEntry).filter(
        models.MealPlanEntry.household_id == household_id,
        models.MealPlanEntry.week_start_date == week_start_date,
    ).all()


@router.get("/favorites", response_model=list[schemas.RecipeOut])
def list_favorite_recipes(household_id: str, db: Session = Depends(get_db)):
    """All recipes this household has rated highly enough to be a favorite."""
    recipe_ids = (
        db.query(models.MealPlanEntry.recipe_id)
        .filter(
            models.MealPlanEntry.household_id == household_id,
            models.MealPlanEntry.is_favorite.is_(True),
        )
        .distinct()
    )
    return db.query(models.Recipe).filter(models.Recipe.id.in_(recipe_ids)).all()
