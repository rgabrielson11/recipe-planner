from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, config_files
from app.database import get_db

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/rejection-reasons")
def get_rejection_reasons():
    """Controlled vocabulary for reason_category. Edit data/rejection_reasons.yaml to extend."""
    return {"reasons": config_files.get_rejection_reasons()}


@router.post("", response_model=schemas.RecipeOut)
def create_recipe(payload: schemas.RecipeCreate, db: Session = Depends(get_db)):
    """
    Minimal recipe stub (source_url + title only). The Phase 2 scraper will
    populate ingredients/instructions/rating on rows created this way; this
    endpoint exists now so rejection tracking has something concrete to
    attach to ahead of that work. Idempotent on source_url.
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
def reject_recipe(recipe_id: str, payload: schemas.RejectionCreate, db: Session = Depends(get_db)):
    """
    Records why a household rejected a suggested recipe option. The
    matching engine (Phase 3) will use this to avoid re-suggesting the same
    recipe, and rejection patterns over time (e.g. repeated
    cook_method_unavailable) can surface as suggested preference updates.
    """
    recipe = db.query(models.Recipe).filter(models.Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    valid_reasons = config_files.get_rejection_reasons()
    if payload.reason_category not in valid_reasons:
        raise HTTPException(
            status_code=422,
            detail=f"reason_category must be one of {valid_reasons}",
        )

    rejection = models.RecipeRejection(recipe_id=recipe_id, **payload.model_dump())
    db.add(rejection)
    db.commit()
    db.refresh(rejection)
    return rejection


@router.get("/{recipe_id}/rejections", response_model=list[schemas.RejectionOut])
def list_rejections_for_recipe(recipe_id: str, db: Session = Depends(get_db)):
    return db.query(models.RecipeRejection).filter(
        models.RecipeRejection.recipe_id == recipe_id
    ).all()
