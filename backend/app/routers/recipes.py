from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, config_files, mealie_client
from app.database import get_db

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/rejection-reasons")
def get_rejection_reasons():
    """Controlled vocabulary for reason_category. Edit data/rejection_reasons.yaml to extend."""
    return {"reasons": config_files.get_rejection_reasons()}


@router.post("/import", response_model=schemas.RecipeOut)
def import_recipe(payload: schemas.RecipeImport, db: Session = Depends(get_db)):
    """
    Imports a recipe into Mealie from a URL (Mealie does the actual
    scraping), then stores a local reference row. Idempotent on
    source_url — re-importing an already-known URL just returns the
    existing local record without calling Mealie again.
    """
    existing = db.query(models.Recipe).filter(
        models.Recipe.source_url == payload.source_url
    ).first()
    if existing:
        return existing

    try:
        slug = mealie_client.import_recipe_from_url(payload.source_url)
        mealie_recipe = mealie_client.get_recipe(slug)
        title = mealie_recipe.get("name", payload.source_url)
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
    Manual recipe stub (source_url + title, no Mealie sync). Useful for
    testing rejection/meal-plan flows without a Mealie call, or for a
    family recipe you don't want imported into Mealie. Idempotent on
    source_url.
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
