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
    Household-wide taste profile, captured during the initial setup
    interview and editable any time afterward.

    - liked_items: ingredients/cuisines to favor when matching recipes
    - disliked_items: SOFT dislikes — recipes containing these are
      deprioritized in scoring but not rejected outright
    - excluded_items: HARD excludes — allergies, intolerances, or "never
      make this" items. Any recipe containing one of these is rejected
      outright by the matching engine, no exceptions.
    """

    __tablename__ = "preferences"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id = Column(
        UUID(as_uuid=False), ForeignKey("households.id"), nullable=False, unique=True
    )
    liked_items = Column(ARRAY(String), nullable=False, default=list)
    disliked_items = Column(ARRAY(String), nullable=False, default=list)
    excluded_items = Column(ARRAY(String), nullable=False, default=list)
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

