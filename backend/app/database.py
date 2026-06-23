"""
SQLite database — Phase 8
Added run_migrations() for safe column additions to existing installs.
"""

import logging
import os
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/recipe_planner.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """
    Safe ALTER TABLE migrations for columns added after initial deployment.
    Each statement is wrapped in try/except — already-existing columns are ignored.
    """
    migrations = [
        ("recipes", "scraped_ingredients_json", "TEXT"),
        ("recipes", "scraped_time_minutes",     "INTEGER"),
        ("recipes", "scraped_description",      "TEXT"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                log.info("Migration: added %s.%s", table, column)
            except Exception:
                pass  # column already exists — safe to ignore
