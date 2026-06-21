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
    recipe_options_per_meal: int = 3
    notes: Optional[str] = None


class PreferenceUpdate(BaseModel):
    liked_items: Optional[list[str]] = None
    disliked_items: Optional[list[str]] = None
    excluded_items: Optional[list[str]] = None
    max_cook_time_minutes: Optional[int] = None
    skill_level: Optional[str] = None
    available_methods: Optional[list[str]] = None
    available_cookware: Optional[list[str]] = None
    recipe_options_per_meal: Optional[int] = None
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
    recipe_options_per_meal: int
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


# ---------- Pantry staples ----------

class StapleCreate(BaseModel):
    name: str


# ---------- Recipes (stub ahead of Phase 2 scraper) ----------

class RecipeCreate(BaseModel):
    source_url: str
    title: str


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_url: str
    title: str
    created_at: datetime


# ---------- Recipe rejections ----------

class RejectionCreate(BaseModel):
    household_id: str
    reason_category: str
    reason_detail: Optional[str] = None


class RejectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    household_id: str
    recipe_id: str
    reason_category: str
    reason_detail: Optional[str]
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
