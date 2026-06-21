from fastapi import FastAPI

from app.database import Base, engine
from app.routers import households, pantry, preferences, recipes

app = FastAPI(title="Recipe Planner API", version="0.1.0")

# MVP: auto-create tables on startup. We'll move to Alembic migrations
# once the schema stabilizes (recipes, meal plans, shopping lists).
Base.metadata.create_all(bind=engine)

app.include_router(households.router)
app.include_router(pantry.router)
app.include_router(preferences.router)
app.include_router(recipes.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
