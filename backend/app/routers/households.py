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
    return household


@router.get("", response_model=list[schemas.HouseholdOut])
def list_households(db: Session = Depends(get_db)):
    return db.query(models.Household).all()


@router.get("/{household_id}", response_model=schemas.HouseholdOut)
def get_household(household_id: str, db: Session = Depends(get_db)):
    household = db.query(models.Household).filter(models.Household.id == household_id).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")
    return household
