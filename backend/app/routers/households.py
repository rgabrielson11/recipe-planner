from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/households", tags=["households"])


@router.post("", response_model=schemas.HouseholdOut)
def create_household(payload: schemas.HouseholdCreate, db: Session = Depends(get_db)):
    household = models.Household(name=payload.name, num_people=payload.num_people)
    db.add(household)
    db.commit()
    db.refresh(household)

    # Patch 14: create a default Preference row up front so the Settings
    # page's Preferences/Equipment tabs never hit a 404 for a brand-new
    # household. GET /preferences/{id} also auto-creates on first read as
    # a fallback for households that predate this change.
    db.add(models.Preference(household_id=household.id))
    db.commit()

    return household


@router.get("", response_model=list[schemas.HouseholdOut])
def list_households(db: Session = Depends(get_db)):
    return db.query(models.Household).all()


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
    """Update household name and/or number of people."""
    hh = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not hh:
        raise HTTPException(status_code=404, detail="Household not found")
    hh.name       = payload.name
    hh.num_people = payload.num_people
    db.commit()
    db.refresh(hh)
    return hh


@router.delete("/{household_id}", status_code=204)
def delete_household(household_id: str, db: Session = Depends(get_db)):
    """Delete a household and all related data (cascades via FK)."""
    hh = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not hh:
        raise HTTPException(status_code=404, detail="Household not found")
    db.delete(hh)
    db.commit()
