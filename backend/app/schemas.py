from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ---------- Household ----------

class HouseholdCreate(BaseModel):
    name: str = "My Household"
    num_people: int = 4


class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    num_people: int
    created_at: datetime


# ---------- Pantry Item ----------

class PantryItemCreate(BaseModel):
    household_id: str
    name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    expiry_date: Optional[date] = None


class PantryItemUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    expiry_date: Optional[date] = None


class PantryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    household_id: str
    name: str
    quantity: Optional[float]
    unit: Optional[str]
    category: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime
    updated_at: datetime
