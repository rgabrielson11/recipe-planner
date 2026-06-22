"""
Recipe Planner API

A professional household meal planning system for a family of 4.

Mission: plan dinner meals using a combination of on-hand ingredients and
weekly shopping trips — minimising food waste, sourcing recipes from credible
sites via Mealie, and building a personalised dinner catalog that improves
every week through the favorites and rejection feedback loops.

Core principles:
  • Pantry-first    — deplete what's on hand before buying anything new
  • Aggregate first — sum all recipe needs, then round once to package sizes
  • Waste-minimal   — carry surplus back into next week's pantry
  • Catalog-growing — rejection suppression keeps recipes in rotation;
                      permanent removes only for genuine dislikes/allergies
  • Credible sources — all recipes flow through Mealie's URL importer,
                       tagged dinner-planner to separate from baking etc.

Weekly workflow:
  1. GET  /meal-plan/pantry-review/{id}              — review what's on hand
  2. POST /meal-plan/week-intent/{id}/{date}          — set hints + # of suggestions
  3. GET  /meal-plan/suggest                          — flat ranked list
  4. POST /recipes/{id}/reject  (optional, per skip) — permanent or temporary
  5. POST /meal-plan/selections                       — lock in chosen recipes
  6. GET  /meal-plan/shopping-list                    — pantry-first list
  7. POST /meal-plan/entries/{id}/review              — end-of-week ratings
"""

from fastapi import FastAPI

from app.database import Base, engine
from app.routers import households, pantry, preferences, recipes, meal_plan

app = FastAPI(
    title="Recipe Planner",
    description=(
        "Professional household meal planning for a family of 4. "
        "Pantry-first, waste-minimal, catalog-growing weekly dinner suggestions "
        "sourced from Mealie-imported recipes on credible cooking sites."
    ),
    version="0.4.0",
)

# Auto-create tables on startup.
# TODO: migrate to Alembic once schema stabilises post Phase 5.
Base.metadata.create_all(bind=engine)

app.include_router(households.router)
app.include_router(pantry.router)
app.include_router(preferences.router)
app.include_router(recipes.router)
app.include_router(meal_plan.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.4.0"}


@app.get("/workflow")
def workflow():
    """Quick reference for the weekly planning workflow."""
    return {
        "weekly_workflow": [
            {"step": 1, "action": "Review pantry",        "endpoint": "GET  /meal-plan/pantry-review/{household_id}"},
            {"step": 2, "action": "Set weekly intent",    "endpoint": "POST /meal-plan/week-intent/{household_id}/{week_start_date}",
             "body": {"ingredient_hints": ["chicken", "salmon"], "num_suggestions": 10}},
            {"step": 3, "action": "Get suggestions",      "endpoint": "GET  /meal-plan/suggest?household_id=...&week_start_date=..."},
            {"step": 4, "action": "Reject skipped (opt)", "endpoint": "POST /recipes/{recipe_id}/reject",
             "note": "permanent=true removes forever; permanent=false suppresses for N weeks"},
            {"step": 5, "action": "Confirm selections",   "endpoint": "POST /meal-plan/selections",
             "body": {"household_id": "...", "week_start_date": "...", "recipe_ids": ["id1", "id2", "id3"]}},
            {"step": 6, "action": "Get shopping list",    "endpoint": "GET  /meal-plan/shopping-list?household_id=...&week_start_date=..."},
            {"step": 7, "action": "Rate meals (end week)","endpoint": "POST /meal-plan/entries/{entry_id}/review",
             "body": {"rating": 4}},
        ]
    }
