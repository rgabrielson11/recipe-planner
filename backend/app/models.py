import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column, String, Integer, ForeignKey, DateTime, Date,
    Numeric, Boolean, Text,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Household(Base):
    __tablename__ = "households"

    id         = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name       = Column(String, nullable=False, default="My Household")
    num_people = Column(Integer, nullable=False, default=4)
    created_at = Column(DateTime, default=datetime.utcnow)

    pantry_items     = relationship("PantryItem",     back_populates="household", cascade="all, delete-orphan")
    preferences      = relationship("Preference",     back_populates="household", uselist=False, cascade="all, delete-orphan")
    weekly_intents   = relationship("WeeklyIntent",   back_populates="household", cascade="all, delete-orphan")
    weekly_selections= relationship("WeeklySelection",back_populates="household", cascade="all, delete-orphan")


class Preference(Base):
    """
    Household-wide taste and cooking profile. All fields editable any time
    via PUT /preferences/{household_id} — no locked interview state.

    Two-tier preference model:
      disliked_items  → SOFT: recipes deprioritised in scoring, never blocked
      excluded_items  → HARD: allergies / never-make; matching engine drops
                              any recipe containing one of these outright

    mealie_dinner_tag → only Mealie recipes carrying this tag are considered
                        for dinner suggestions. Set to "" to use all recipes.
                        Default: "dinner-planner"

    default_num_suggestions → how many recipes to pull per weekly suggestion
                               run. The household can override this week-by-week
                               in the WeeklyIntent; this is the fallback default.
    """
    __tablename__ = "preferences"

    id                      = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id            = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False, unique=True)
    liked_items             = Column(ARRAY(String), nullable=False, default=list)
    disliked_items          = Column(ARRAY(String), nullable=False, default=list)
    excluded_items          = Column(ARRAY(String), nullable=False, default=list)
    max_cook_time_minutes   = Column(Integer, nullable=True)
    skill_level             = Column(String, nullable=True)   # beginner | intermediate | advanced
    available_methods       = Column(ARRAY(String), nullable=False, default=list)
    available_cookware      = Column(ARRAY(String), nullable=False, default=list)
    recipe_options_per_meal = Column(Integer, nullable=False, default=3)
    default_num_suggestions = Column(Integer, nullable=False, default=10)   # flat suggestion pool size
    favorite_rating_threshold = Column(Integer, nullable=False, default=4)  # ≥ this → is_favorite
    mealie_dinner_tag       = Column(String, nullable=False, default="dinner-planner")
    notes                   = Column(String, nullable=True)
    created_at              = Column(DateTime, default=datetime.utcnow)
    updated_at              = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="preferences")


class PantryItem(Base):
    __tablename__ = "pantry_items"

    id           = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    name         = Column(String, nullable=False, index=True)
    quantity     = Column(Numeric, nullable=True)
    unit         = Column(String, nullable=True)
    category     = Column(String, nullable=True)   # produce | pantry | dairy | freezer | meat | etc.
    expiry_date  = Column(Date, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="pantry_items")


class Recipe(Base):
    """
    Local reference row for a recipe whose full content lives in Mealie.
    mealie_slug is Mealie's identifier; title and source_url are cached
    locally for display without an extra Mealie call.
    A recipe with no mealie_slug is a pending stub (Mealie import pending).
    """
    __tablename__ = "recipes"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    source_url  = Column(String, nullable=False, unique=True)
    title       = Column(String, nullable=False)
    mealie_slug = Column(String, nullable=True, unique=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    rejections       = relationship("RecipeRejection", back_populates="recipe", cascade="all, delete-orphan")
    meal_plan_entries= relationship("MealPlanEntry",   back_populates="recipe", cascade="all, delete-orphan")
    selections       = relationship("WeeklySelection", back_populates="recipe", cascade="all, delete-orphan")


class RecipeRejection(Base):
    """
    Records why a household passed on a suggested recipe.

    Two tiers (driven by rejection_reasons.yaml):
      is_permanent=True  → matching engine excludes this recipe forever
                           (dislike, allergy, missing equipment)
      is_permanent=False → matching engine suppresses for `suppress_weeks`
                           weeks starting from `rejected_week`, then the
                           recipe resurfaces in future suggestion pools
                           (not_this_week, already_made_recently, etc.)

    This two-tier model keeps the catalog growing rather than shrinking
    every time the household passes on something for a situational reason.
    """
    __tablename__ = "recipe_rejections"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id    = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    recipe_id       = Column(UUID(as_uuid=False), ForeignKey("recipes.id"), nullable=False)
    reason_category = Column(String, nullable=False)   # key from rejection_reasons.yaml
    reason_detail   = Column(String, nullable=True)    # free-text elaboration
    is_permanent    = Column(Boolean, nullable=False, default=True)
    rejected_week   = Column(Date, nullable=True)      # week_start_date this rejection was recorded
    suppress_weeks  = Column(Integer, nullable=True)   # None for permanent rejections
    created_at      = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="rejections")


class WeeklyIntent(Base):
    """
    Per-week planning intent, recorded at the start of the weekly session.

    ingredient_hints: free-text keywords the household wants to feature
      this week — proteins, cuisines, themes. e.g. ["chicken thighs",
      "salmon", "bbq", "quick weeknight"]. Each hint boosts recipe scores
      by +15 pts (max +45) for this week only — stronger than the permanent
      liked_items signal (+5 each) so the week's theme dominates.

    num_suggestions: how many recipes to pull in the flat suggestion list
      this week. Overrides default_num_suggestions from preferences.
      Varies week to week — some weeks you want 5 options, some 15.

    pantry_snapshot_notes: free-text captured during the pantry check-in.
      Not used by the engine; serves as a human-readable record.
    """
    __tablename__ = "weekly_intents"

    id                    = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id          = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    week_start_date       = Column(Date, nullable=False)
    ingredient_hints      = Column(ARRAY(String), nullable=False, default=list)
    num_suggestions       = Column(Integer, nullable=True)   # None → use prefs default
    pantry_snapshot_notes = Column(Text, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="weekly_intents")


class WeeklySelection(Base):
    """
    The household's chosen recipes for a given week, locked in after reviewing
    the suggestion list. One row per selected recipe per week.

    The shopping list generator reads ONLY these selections — not the full
    suggestion pool. Recipes not selected are either rejected (with a reason)
    or simply skipped; both are valid. Only explicit rejections (POST
    /recipes/{id}/reject) feed back into the matching engine's learning.

    End-of-week rating flows through MealPlanEntry (created when selections
    are confirmed) so the favorites loop still works unchanged.
    """
    __tablename__ = "weekly_selections"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id    = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    recipe_id       = Column(UUID(as_uuid=False), ForeignKey("recipes.id"), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    household = relationship("Household", back_populates="weekly_selections")
    recipe    = relationship("Recipe",    back_populates="selections")


class MealPlanEntry(Base):
    """
    Created automatically when a WeeklySelection is confirmed. Holds the
    end-of-week star rating and the favorites loop state.

    is_favorite is set when rating >= household's favorite_rating_threshold.
    The matching engine uses this to surface proven winners in future weeks.
    """
    __tablename__ = "meal_plan_entries"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id    = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    recipe_id       = Column(UUID(as_uuid=False), ForeignKey("recipes.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    rating          = Column(Integer, nullable=True)   # 1-5, set during end-of-week review
    is_favorite     = Column(Boolean, nullable=False, default=False)
    reviewed_at     = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="meal_plan_entries")
