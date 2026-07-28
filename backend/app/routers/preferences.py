from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, config_files
from app.database import get_db

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("/vocabulary")
def get_interview_vocabulary():
    """
    Valid options for skill_level, available_methods, and available_cookware.
    The interview UI (or a re-run "update my preferences" flow) should pull
    this list rather than hardcoding choices, so adding a new cooking method
    later (e.g. a new appliance) only requires editing
    backend/app/data/cooking_vocabulary.yaml.
    """
    return config_files.get_cooking_vocabulary()


@router.post("", response_model=schemas.PreferenceOut)
def create_preferences(payload: schemas.PreferenceCreate, db: Session = Depends(get_db)):
    """
    Used during the initial deployment interview to capture a household's
    taste profile. One household has exactly one preference record —
    calling this again for the same household will error; use PUT to edit.
    """
    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    existing = db.query(models.Preference).filter(
        models.Preference.household_id == payload.household_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Preferences already exist for this household. Use PUT /preferences/{household_id} to edit.",
        )

    prefs = models.Preference(**payload.model_dump())
    db.add(prefs)
    db.commit()
    db.refresh(prefs)
    return prefs


@router.get("/{household_id}", response_model=schemas.PreferenceOut)
def get_preferences(household_id: str, db: Session = Depends(get_db)):
    """
    Returns this household's preferences, auto-creating a row with sane
    defaults on first read if one doesn't exist yet (Patch 14). There was
    never a UI flow that called POST /preferences for a new household, so
    without this every fresh household 404'd here forever and the Settings
    page's Preferences/Equipment tabs silently rendered blank.
    """
    prefs = db.query(models.Preference).filter(
        models.Preference.household_id == household_id
    ).first()
    if not prefs:
        household = db.query(models.Household).filter(
            models.Household.id == household_id
        ).first()
        if not household:
            raise HTTPException(status_code=404, detail="Household not found")
        prefs = models.Preference(household_id=household_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


@router.put("/{household_id}", response_model=schemas.PreferenceOut)
def update_preferences(household_id: str, payload: schemas.PreferenceUpdate, db: Session = Depends(get_db)):
    """
    Lets a household adjust likes/dislikes/excludes any time after the
    initial interview — preferences are never locked in.
    """
    prefs = db.query(models.Preference).filter(
        models.Preference.household_id == household_id
    ).first()
    if not prefs:
        raise HTTPException(status_code=404, detail="No preferences set for this household yet")

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(prefs, field, value)

    db.commit()
    db.refresh(prefs)
    return prefs
