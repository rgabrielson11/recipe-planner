import uuid
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Date, Numeric, Boolean
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Household(Base):
    __tablename__ = "households"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False, default="My Household")
    num_people = Column(Integer, nullable=False, default=4)
    created_at = Column(DateTime, default=datetime.utcnow)

    pantry_items = relationship(
        "PantryItem", back_populates="household", cascade="all, delete-orphan"
    )
    preferences = relationship(
        "Preference",
        back_populates="household",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Preference(Base):
    """
    Household-wide taste and cooking profile, captured during the initial
    setup interview and editable any time afterward via the same endpoints
    (PUT /preferences/{household_id}) — there is no "locked" interview;
    re-running it later (e.g. new appliance, changed tastes) is just an
    update to the same record.

    - liked_items: ingredients/cuisines to favor when matching recipes
    - disliked_items: SOFT dislikes — recipes containing these are
      deprioritized in scoring but not rejected outright
    - excluded_items: HARD excludes — allergies, intolerances, or "never
      make this" items. Any recipe containing one of these is rejected
      outright by the matching engine, no exceptions.
    - max_cook_time_minutes: recipes above this active+total time are
      deprioritized/filtered depending on matching engine strictness setting
    - skill_level: beginner / intermediate / advanced — filters recipe
      complexity
    - available_methods: cooking methods the household can actually use
      (e.g. oven, stovetop, grill, slow_cooker, instant_pot, air_fryer,
      sous_vide, smoker, microwave) — recipes requiring an unavailable
      method are excluded
    - available_cookware: specific equipment on hand (e.g. dutch_oven,
      cast_iron_skillet, wok, sheet_pan, stand_mixer, blender,
      food_processor, immersion_blender) — same exclusion behavior
    - recipe_options_per_meal: how many candidate recipes to offer per meal
      slot when generating a weekly plan, rather than a single auto-pick
    - notes: free-form catch-all for anything else (e.g. "no deep frying
      indoors", "kids won't eat anything spicy")
    """

    __tablename__ = "preferences"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(
        UUID(as_uuid=False), ForeignKey("households.id"), nullable=False, unique=True
    )
    liked_items = Column(ARRAY(String), nullable=False, default=list)
    disliked_items = Column(ARRAY(String), nullable=False, default=list)
    excluded_items = Column(ARRAY(String), nullable=False, default=list)
    max_cook_time_minutes = Column(Integer, nullable=True)
    skill_level = Column(String, nullable=True)  # beginner | intermediate | advanced
    available_methods = Column(ARRAY(String), nullable=False, default=list)
    available_cookware = Column(ARRAY(String), nullable=False, default=list)
    recipe_options_per_meal = Column(Integer, nullable=False, default=3)
    favorite_rating_threshold = Column(Integer, nullable=False, default=4)  # star rating (1-5) at/above which a reviewed recipe becomes a favorite
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="preferences")


class PantryItem(Base):
    __tablename__ = "pantry_items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    name = Column(String, nullable=False, index=True)
    quantity = Column(Numeric, nullable=True)
    unit = Column(String, nullable=True)
    category = Column(String, nullable=True)  # e.g. produce, pantry, dairy, freezer
    expiry_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    household = relationship("Household", back_populates="pantry_items")


class Recipe(Base):
    """
    A local reference to a recipe stored in Mealie (the actual recipe
    content — ingredients, instructions, image — lives there, not here).
    `mealie_slug` is Mealie's identifier for the recipe; `source_url` and
    `title` are cached locally for display without an extra Mealie call.

    A recipe with no `mealie_slug` yet is a "pending" stub — e.g. created
    by POST /recipes before the Mealie import call completes/succeeds, or
    if Mealie was unreachable. The matching engine should treat these as
    not-yet-usable until a slug is set.
    """

    __tablename__ = "recipes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    source_url = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    mealie_slug = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    rejections = relationship(
        "RecipeRejection", back_populates="recipe", cascade="all, delete-orphan"
    )
    meal_plan_entries = relationship(
        "MealPlanEntry", back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeRejection(Base):
    """
    Records why a household rejected a suggested recipe. reason_category
    must be one of the values in data/rejection_reasons.yaml. This is
    feedback data for the matching engine (don't re-suggest, and over time
    surface patterns — e.g. repeated cook_method_unavailable rejections on
    a method might prompt updating available_methods).
    """

    __tablename__ = "recipe_rejections"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    recipe_id = Column(UUID(as_uuid=False), ForeignKey("recipes.id"), nullable=False)
    reason_category = Column(String, nullable=False)
    reason_detail = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="rejections")


class MealPlanEntry(Base):
    """
    One recipe slotted into one household's plan for a given week.
    Captures the weekly review/favorites loop: a recipe is added to a
    week's plan, then after the week, reviewed with a 1-5 star rating.
    If the rating meets or exceeds the household's
    favorite_rating_threshold, is_favorite is set and the recipe is
    tagged/favorited back in Mealie too (best-effort — see
    app/mealie_client.py).

    The Phase 3 matching engine should pull a deliberate MIX of
    is_favorite=True recipes (repeats of what's worked before) and
    never-yet-rated recipes (new discoveries) when building each week's
    options — never all-repeat, never all-untested.
    """

    __tablename__ = "meal_plan_entries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    recipe_id = Column(UUID(as_uuid=False), ForeignKey("recipes.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    rating = Column(Integer, nullable=True)  # 1-5, set during weekly review
    is_favorite = Column(Boolean, nullable=False, default=False)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    recipe = relationship("Recipe", back_populates="meal_plan_entries")

