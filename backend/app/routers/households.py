"""
Household endpoints.

The app is single-household: one Household row holds the family name and
number of people (used for serving scaling).  The household id is still the
key on pantry, preferences, selections, etc., but the frontend never asks for
it — it loads GET /households/current on start-up.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/households", tags=["households"])


def get_or_create_household(db: Session) -> models.Household:
    """Return the household (oldest if several exist), creating it on first run."""
    hh = db.query(models.Household).order_by(models.Household.created_at).first()
    if hh:
        return hh
    hh = models.Household(name="Household", num_people=4)
    db.add(hh)
    db.commit()
    db.refresh(hh)
    # Default Preference row so Settings never 404s on a fresh install.
    db.add(models.Preference(household_id=hh.id))
    db.commit()
    return hh


@router.get("/current", response_model=schemas.HouseholdOut)
def get_current_household(db: Session = Depends(get_db)):
    """The single household this install plans for."""
    return get_or_create_household(db)


@router.get("/{household_id}", response_model=schemas.HouseholdOut)
def get_household(household_id: str, db: Session = Depends(get_db)):
    hh = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not hh:
        raise HTTPException(status_code=404, detail="Household not found")
    return hh


@router.put("/{household_id}", response_model=schemas.HouseholdOut)
def update_household(
    household_id: str,
    payload: schemas.HouseholdCreate,
    db: Session = Depends(get_db),
):
    """Update household name and/or number of people (Settings → Preferences)."""
    hh = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not hh:
        raise HTTPException(status_code=404, detail="Household not found")
    hh.name       = payload.name
    hh.num_people = payload.num_people
    db.commit()
    db.refresh(hh)
    return hh
