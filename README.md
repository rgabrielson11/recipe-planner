# Recipe Planner

Self-hosted meal planning app: tracks on-hand pantry ingredients, scrapes
trusted recipe sites for matches, generates weekly shopping lists, and
exports recipes to PDF. Built to run on Unraid via Docker Compose.

## Status: Phase 1 — Pantry CRUD (this commit)

Working: household profile + pantry item CRUD via a FastAPI backend and
Postgres database.

### Roadmap
1. ✅ Data model + pantry CRUD
2. ⬜ Recipe scraper module (`recipe-scrapers` + curated site allowlist)
3. ⬜ Matching engine (pantry overlap % + rating threshold)
4. ⬜ Interview flow (servings, diet, cuisine, cook-time prefs)
5. ⬜ Weekly plan generator → shopping list → Apple Reminders export, PDF recipe export

## Running locally / on Unraid

1. Copy `.env.example` to `.env` and adjust credentials.
2. From the repo root:
   ```bash
   docker compose up -d --build
   ```
3. API available at `http://<unraid-ip>:8000`. Interactive docs at
   `http://<unraid-ip>:8000/docs`.

On Unraid specifically: place this repo under `/mnt/user/appdata/recipe-planner`,
then either run `docker compose` from the Unraid terminal, or recreate the two
services as Unraid Docker templates pointing at the same `docker-compose.yml`
(Community Applications → "Add Container" → point Repository at the built image,
or use the **Compose Manager** plugin to run this file directly).

## Shopping list design principles

The shopping list generator (Phase 5) is built around minimizing waste and
unnecessary purchases:

1. **Pantry-first matching** — on-hand stock is depleted across the *entire*
   week's meal plan before anything is added to the shopping list, not
   recipe-by-recipe.
2. **Aggregate before rounding** — each ingredient's total need is summed
   across all of the week's recipes first, then rounded up once, rather than
   buying a full package per recipe that calls for it.
3. **Real package sizes** — quantities round up to what's actually sold
   (dozen eggs, half-gallon milk, 1 lb butter box, etc.), using
   `backend/app/data/package_sizes.json` as the reference table. This table
   is intentionally small to start and expands as new ingredients are
   encountered.
4. **Surplus carry-forward** — when a rounded purchase exceeds what's needed
   that week, the leftover is written back into the pantry as expected
   on-hand stock for next week's planning, closing the waste-reduction loop.

## API quickstart

```bash
# Get valid options for skill level / cooking methods / cookware (drives the interview UI)
curl http://localhost:8000/preferences/vocabulary

# Create a household
curl -X POST http://localhost:8000/households \
  -H "Content-Type: application/json" \
  -d '{"name": "The Smiths", "num_people": 4}'

# Initial deployment interview: capture taste + cooking profile (use the household id above)
curl -X POST http://localhost:8000/preferences \
  -H "Content-Type: application/json" \
  -d '{
        "household_id": "<id>",
        "liked_items": ["italian", "mexican", "garlic", "spicy"],
        "disliked_items": ["cilantro", "blue cheese"],
        "excluded_items": ["peanuts", "shellfish"],
        "max_cook_time_minutes": 45,
        "skill_level": "intermediate",
        "available_methods": ["stovetop", "oven", "instant_pot_pressure_cooker", "air_fryer"],
        "available_cookware": ["sheet_pan", "dutch_oven", "cast_iron_skillet", "blender"],
        "notes": "No deep frying indoors. Kids won'\''t eat anything spicy."
      }'

# Re-run part of the interview later (new air fryer, tastes changed, etc.) — partial update, nothing is locked in
curl -X PUT http://localhost:8000/preferences/<household_id> \
  -H "Content-Type: application/json" \
  -d '{"available_methods": ["stovetop", "oven", "instant_pot_pressure_cooker", "air_fryer", "sous_vide"]}'

# Add a pantry item
curl -X POST http://localhost:8000/pantry \
  -H "Content-Type: application/json" \
  -d '{"household_id": "<id>", "name": "olive oil", "quantity": 1, "unit": "bottle", "category": "pantry"}'

# List pantry items
curl http://localhost:8000/pantry?household_id=<id>
```

### Preference semantics

- **`liked_items`** — ingredients/cuisines to favor in recipe scoring (Phase 3).
- **`disliked_items`** — SOFT excludes. A recipe containing one of these is
  deprioritized but can still surface if it's otherwise a strong match.
- **`excluded_items`** — HARD excludes. Allergies, intolerances, or "never
  make this" items. Any recipe containing one of these is rejected outright,
  no exceptions, no scoring override.
- **`max_cook_time_minutes`**, **`skill_level`** — filter out recipes that
  are too long or too advanced for the household.
- **`available_methods`** / **`available_cookware`** — hard filters too: a
  recipe requiring a smoker or stand mixer the household doesn't have is
  excluded, same as an allergy. Valid values come from
  `GET /preferences/vocabulary` / `backend/app/data/cooking_vocabulary.json`,
  which is meant to be extended over time (e.g. a new appliance) without a
  schema change.
- **Nothing here is locked in.** Every field is editable any time via
  `PUT /preferences/{household_id}` — there's no separate "re-run the
  interview" mechanism, just the same update endpoint used to keep the
  profile current as tastes, equipment, or schedules change.


## Tech stack

- **Backend:** FastAPI + SQLAlchemy
- **Database:** PostgreSQL 16
- **Deployment:** Docker Compose (Unraid-friendly)
- **Recipe sourcing (upcoming):** `recipe-scrapers` library against an
  allowlist of trusted sites (Serious Eats, Food Network, AllRecipes, etc).
  NYT Cooking is paywalled and will be a manual "paste URL" fallback rather
  than automated discovery.

## Version control

This repo is git-initialized locally. To push to GitHub:

```bash
gh repo create recipe-planner --private --source=. --remote=origin
# or, without gh CLI:
git remote add origin git@github.com:<your-username>/recipe-planner.git
git branch -M main
git push -u origin main
```
