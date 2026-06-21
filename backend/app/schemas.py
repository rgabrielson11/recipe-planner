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


# ---------- Preferences (taste profile / initial interview) ----------

class PreferenceCreate(BaseModel):
    household_id: str
    liked_items: list[str] = []
    disliked_items: list[str] = []  # soft: deprioritize, don't reject
    excluded_items: list[str] = []  # hard: allergies / never make
    max_cook_time_minutes: Optional[int] = None
    skill_level: Optional[str] = None  # beginner | intermediate | advanced
    available_methods: list[str] = []  # e.g. oven, stovetop, grill, slow_cooker, instant_pot, air_fryer
    available_cookware: list[str] = []  # e.g. dutch_oven, cast_iron_skillet, wok, sheet_pan, stand_mixer
    notes: Optional[str] = None


class PreferenceUpdate(BaseModel):
    liked_items: Optional[list[str]] = None
    disliked_items: Optional[list[str]] = None
    excluded_items: Optional[list[str]] = None
    max_cook_time_minutes: Optional[int] = None
    skill_level: Optional[str] = None
    available_methods: Optional[list[str]] = None
    available_cookware: Optional[list[str]] = None
    notes: Optional[str] = None


class PreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    household_id: str
    liked_items: list[str]
    disliked_items: list[str]
    excluded_items: list[str]
    max_cook_time_minutes: Optional[int]
    skill_level: Optional[str]
    available_methods: list[str]
    available_cookware: list[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


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
