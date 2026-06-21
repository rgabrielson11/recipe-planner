import uuid
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Date, Numeric
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

