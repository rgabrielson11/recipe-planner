## [0.6.1] — Hotfix — 2026-06-22

### Fixed
- **"Household not found" on startup** — frontend now validates the stored
  `householdId` against the API on every page load. If the record no longer
  exists (e.g. DB was wiped, fresh container deploy), localStorage is cleared
  automatically and the app falls back to the setup screen. No more manual
  browser devtools intervention needed.
- **`PUT /households/{id}` was missing** — Settings screen save was returning
  405 Method Not Allowed when trying to update household name or people count.
  Endpoint added. `DELETE /households/{id}` also added for completeness.

# Changelog

## [0.6.0] — Phase 6 — 2026-06-22

### Added
- **Recipe discovery engine** (`recipe_discovery.py`)
  - Fetches category pages from curated sites in `recipe_sources.yaml`
  - Extracts recipe URLs via BeautifulSoup, filters already-known URLs
  - Scrapes new recipes using the `recipe-scrapers` library
  - Scores against pantry + preferences using the same engine as Mealie scoring
  - Creates stub `Recipe` rows (mealie_slug=None) for newly found recipes
- **`recipe_sources.yaml`** — 11 curated sites with category URLs, fully hand-editable
  - Serious Eats, Simply Recipes, Budget Bytes, Half Baked Harvest, Cookie and Kate,
    The Kitchn, AllRecipes, Food Network, Skinnytaste, Pinch of Yum, Damn Delicious
  - Per-source `enabled: false` to pause without deleting
  - `discovery` settings block: max_scraped_per_run, request_delay_seconds,
    mealie_min_rating, mealie_favorites_count
- **Auto-import on selection** (`routers/meal_plan.py`)
  - When `POST /meal-plan/selections` is confirmed, any selected recipe that has
    no `mealie_slug` yet is automatically imported into Mealie
  - The dinner-planner tag is applied to the imported recipe so it enters the
    Mealie pool in future weeks
  - Response now includes `mealie_imports` with per-recipe import status
- **`mealie_client.add_tag_to_recipe()`** — adds a tag without removing existing ones
- **`mealie_client.get_top_rated_recipes()`** — fetches Mealie recipes above min_rating
- `requirements.txt` — added `recipe-scrapers==14.55.0`, `beautifulsoup4==4.12.3`, `lxml==5.2.1`

### Changed
- **Matching engine** (`matching_engine.py`) — dual-pool architecture:
  - **Pool A (Mealie favourites):** 1–2 slots for proven 4★+ Mealie recipes
  - **Pool B (Discovery):** remaining slots filled by newly scraped recipes
  - `WeeklySuggestion` response now includes `mealie_favorites_shown` and
    `discoveries_shown` instead of the old `favorites_in_pool`/`discoveries_in_pool`
- `schemas.py` — `WeeklySelectionSummary` gains `mealie_imports` list

### How the catalog grows
Each weekly session:
  1. `GET /meal-plan/suggest` discovers N new recipes from curated sites
  2. Household picks 2-5 of them
  3. `POST /meal-plan/selections` auto-imports picked recipes into Mealie
     and applies the `dinner-planner` tag
  4. End-of-week ratings promote good recipes to 4★+
  5. Next week: those 4★+ recipes appear in Pool A as proven favourites

## [0.5.0] — Phase 5 — 2026-06-22
React SPA frontend, SQLite single container, Pantry Check section, port 8111.

## [0.4.0] — Phase 4 — 2026-06-22
WeeklySelection, num_suggestions per week, two-tier rejection, shopping list.

## [0.3.0] — Phase 3 — 2026-06-22
Matching engine, Mealie tag filter, weekly intent hints.

## [0.2.0] — Phase 2 — 2026-06-21
Mealie recipe import, rejection reasons, weekly review/favourites loop.

## [0.1.0] — Phase 1 — 2026-06-21
Household + preferences CRUD, pantry + staples, Docker Compose, YAML configs.
