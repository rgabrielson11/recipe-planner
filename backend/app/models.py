"""
SQLAlchemy models — Phase 8
Added scraped_ingredients_json to Recipe for shopping list fallback.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Date, Numeric, Boolean, Text, JSON
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Household(Base):
    __tablename__ = "households"
    id         = Column(String, primary_key=True, default=gen_uuid)
    name       = Column(String, nullable=False, default="My Household")
    num_people = Column(Integer, nullable=False, default=4)
    created_at = Column(DateTime, default=datetime.utcnow)

    pantry_items      = relationship("PantryItem",      back_populates="household", cascade="all, delete-orphan")
    preferences       = relationship("Preference",      back_populates="household", uselist=False, cascade="all, delete-orphan")
    weekly_intents    = relationship("WeeklyIntent",    back_populates="household", cascade="all, delete-orphan")
    weekly_selections = relationship("WeeklySelection", back_populates="household", cascade="all, delete-orphan")


class Preference(Base):
    __tablename__ = "preferences"
    id                        = Column(String, primary_key=True, default=gen_uuid)
    household_id              = Column(String, ForeignKey("households.id"), nullable=False, unique=True)
    liked_items               = Column(JSON, nullable=False, default=list)
    disliked_items            = Column(JSON, nullable=False, default=list)
    excluded_items            = Column(JSON, nullable=False, default=list)
    max_cook_time_minutes     = Column(Integer, nullable=True)
    skill_level               = Column(String, nullable=True)
    available_methods         = Column(JSON, nullable=False, default=list)
    available_cookware        = Column(JSON, nullable=False, default=list)
    recipe_options_per_meal   = Column(Integer, nullable=False, default=3)
    default_num_suggestions   = Column(Integer, nullable=False, default=10)
    favorite_rating_threshold = Column(Integer, nullable=False, default=4)
    mealie_dinner_tag         = Column(String, nullable=False, default="dinner-planner")
    notes                     = Column(String, nullable=True)
    created_at                = Column(DateTime, default=datetime.utcnow)
    updated_at                = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="preferences")


class PantryItem(Base):
    __tablename__ = "pantry_items"
    id           = Column(String, primary_key=True, default=gen_uuid)
    household_id = Column(String, ForeignKey("households.id"), nullable=False)
    name         = Column(String, nullable=False, index=True)
    quantity     = Column(Numeric, nullable=True)
    unit         = Column(String, nullable=True)
    category     = Column(String, nullable=True)
    expiry_date  = Column(Date, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="pantry_items")


class Recipe(Base):
    __tablename__ = "recipes"
    id                        = Column(String, primary_key=True, default=gen_uuid)
    source_url                = Column(String, nullable=False, unique=True)
    title                     = Column(String, nullable=False)
    mealie_slug               = Column(String, nullable=True, unique=True)
    scraped_ingredients_json  = Column(Text, nullable=True)   # JSON list[str] — fallback for shopping list
    scraped_time_minutes      = Column(Integer, nullable=True)
    scraped_description       = Column(Text, nullable=True)
    created_at                = Column(DateTime, default=datetime.utcnow)

    rejections        = relationship("RecipeRejection", back_populates="recipe", cascade="all, delete-orphan")
    meal_plan_entries = relationship("MealPlanEntry",   back_populates="recipe", cascade="all, delete-orphan")
    selections        = relationship("WeeklySelection", back_populates="recipe", cascade="all, delete-orphan")


class RecipeRejection(Base):
    __tablename__ = "recipe_rejections"
    id              = Column(String, primary_key=True, default=gen_uuid)
    household_id    = Column(String, ForeignKey("households.id"), nullable=False)
    recipe_id       = Column(String, ForeignKey("recipes.id"), nullable=False)
    reason_category = Column(String, nullable=False)
    reason_detail   = Column(String, nullable=True)
    is_permanent    = Column(Boolean, nullable=False, default=True)
    rejected_week   = Column(Date, nullable=True)
    suppress_weeks  = Column(Integer, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="rejections")


class WeeklyIntent(Base):
    __tablename__ = "weekly_intents"
    id                    = Column(String, primary_key=True, default=gen_uuid)
    household_id          = Column(String, ForeignKey("households.id"), nullable=False)
    week_start_date       = Column(Date, nullable=False)
    ingredient_hints      = Column(JSON, nullable=False, default=list)
    num_suggestions       = Column(Integer, nullable=True)
    pantry_snapshot_notes = Column(Text, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="weekly_intents")


class WeeklySelection(Base):
    __tablename__ = "weekly_selections"
    id              = Column(String, primary_key=True, default=gen_uuid)
    household_id    = Column(String, ForeignKey("households.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    recipe_id       = Column(String, ForeignKey("recipes.id"), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    household = relationship("Household", back_populates="weekly_selections")
    recipe    = relationship("Recipe",    back_populates="selections")


class MealPlanEntry(Base):
    __tablename__ = "meal_plan_entries"
    id              = Column(String, primary_key=True, default=gen_uuid)
    household_id    = Column(String, ForeignKey("households.id"), nullable=False)
    recipe_id       = Column(String, ForeignKey("recipes.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    rating          = Column(Integer, nullable=True)
    is_favorite     = Column(Boolean, nullable=False, default=False)
    reviewed_at     = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="meal_plan_entries")
