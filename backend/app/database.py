"""
SQLite database — Phase 10
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
        ("recipes", "last_scraped_at",          "DATETIME"),
        ("recipes", "scraped_instructions_json", "TEXT"),      # print support
        ("recipes", "scraped_servings",          "TEXT"),      # e.g. "4 servings"
        ("recipes", "scraped_carbs",            "REAL"),      # grams carbs per serving
        ("recipes", "scraped_tokens_json",      "TEXT"),      # Patch 12
        ("recipes", "scraped_rating",           "FLOAT"),     # Patch 13
        ("recipes", "scraped_reviews",          "INTEGER"),   # Patch 13
        ("preferences", "bring_list_name",      "TEXT"),      # Patch 16
        ("preferences", "bring_shopping_enabled",  "BOOLEAN DEFAULT 1"),  # Phase 11
        ("preferences", "bring_ollama_normalize", "BOOLEAN DEFAULT 1"),  # Phase 11
        ("preferences", "ha_shopping_enabled",      "BOOLEAN DEFAULT 0"),  # Phase 11
        ("preferences", "ha_shopping_list_entity",  "TEXT"),                  # Phase 11
        ("weekly_selections", "servings_override", "INTEGER"),   # Patch 69
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                log.info("Migration: added %s.%s", table, column)
            except Exception:
                pass  # column already exists — safe to ignore


def tune_sqlite():
    """
    Patch 12: WAL journal mode so the nightly background scraper (writer)
    never blocks daytime suggest runs (readers), plus indexes on the columns
    the discovery cache filters on.  All statements are idempotent.
    """
    statements = [
        "PRAGMA journal_mode=WAL",
        "CREATE INDEX IF NOT EXISTS ix_recipes_last_scraped_at ON recipes(last_scraped_at)",
        "CREATE INDEX IF NOT EXISTS ix_recipe_rejections_recipe_id ON recipe_rejections(recipe_id)",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                log.warning("tune_sqlite: %s failed: %s", stmt, e)
    log.info("SQLite tuned: WAL mode + discovery indexes")
