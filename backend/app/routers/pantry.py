from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, config_files
from app.database import get_db

router = APIRouter(prefix="/pantry", tags=["pantry"])


@router.get("/staples")
def get_staples():
    """
    Always-on-hand ingredients (salt, oil, flour, etc). Backed by
    backend/app/data/pantry_staples.yaml — hand-editable in VS Code, or
    managed through these endpoints. Either way edits land in the same
    file and comments are preserved.
    """
    return {"staples": config_files.get_staples()}


@router.post("/staples")
def add_staple(payload: schemas.StapleCreate):
    return {"staples": config_files.add_staple(payload.name)}


@router.delete("/staples/{name}")
def delete_staple(name: str):
    return {"staples": config_files.remove_staple(name)}


@router.post("", response_model=schemas.PantryItemOut)
def create_pantry_item(payload: schemas.PantryItemCreate, db: Session = Depends(get_db)):
    household = db.query(models.Household).filter(
        models.Household.id == payload.household_id
    ).first()
    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    item = models.PantryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[schemas.PantryItemOut])
def list_pantry_items(household_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.PantryItem)
    if household_id:
        query = query.filter(models.PantryItem.household_id == household_id)
    return query.order_by(models.PantryItem.name).all()


@router.get("/{item_id}", response_model=schemas.PantryItemOut)
def get_pantry_item(item_id: str, db: Session = Depends(get_db)):
    item = db.query(models.PantryItem).filter(models.PantryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return item


@router.put("/{item_id}", response_model=schemas.PantryItemOut)
def update_pantry_item(item_id: str, payload: schemas.PantryItemUpdate, db: Session = Depends(get_db)):
    item = db.query(models.PantryItem).filter(models.PantryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pantry item not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_pantry_item(item_id: str, db: Session = Depends(get_db)):
    item = db.query(models.PantryItem).filter(models.PantryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pantry item not found")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}
