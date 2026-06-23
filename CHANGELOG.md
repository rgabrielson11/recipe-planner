# Changelog

## [0.5.0] — Phase 5 — 2026-06-22

### Added
- **Frontend** — React SPA served from FastAPI at port 8111
  - Home dashboard with weekly status checklist
  - Pantry screen: on-hand items (by category, expiry flags) + staples management
  - Plan screen: weekly intent (hints + suggestion count) → flat ranked suggestions
    → select / skip with two-tier rejection modal → confirm selections
  - Shopping screen: buy list by store section (collapsible, checkboxes),
    Pantry Check section (staples with required quantities), Using from Pantry section
  - Review screen: end-of-week star ratings → favourites loop
  - Settings screen: household, Mealie tag, suggestion defaults, full preference management
- **Single container** — PostgreSQL replaced with SQLite; no separate DB service
- **Pantry Check section** on shopping list — staples assumed on hand but listed
  with required quantities as a final verify-before-you-shop step; separate from buy list

### Changed
- `docker-compose.yml` now defines a single `app` service (was `api` + `db`)
- Port changed to **8111** throughout
- All API routes prefixed with `/api`; frontend served at `/`
- `models.py` — `ARRAY(String)` replaced with `JSON` for SQLite compatibility
- `database.py` — SQLite with `check_same_thread=False`
- `requirements.txt` — removed `psycopg2-binary`, SQLite is built into Python
- `shopping_list.py` — staples now produce `pantry_check` entries with quantities
  instead of a flat `staples_relied_on` list
- `schemas.py` — `ShoppingList.pantry_check` field replaces `staples_relied_on`

## [0.4.0] — Phase 4 — 2026-06-22

### Added
- `WeeklySelection` model + `POST /api/meal-plan/selections`
- `WeeklyIntent.num_suggestions` — variable suggestion count per week
- `Preference.default_num_suggestions` — household default (overridden per-week)
- Two-tier rejection: permanent (never resurface) vs temporary (suppress N weeks)
- `RecipeRejection.is_permanent`, `rejected_week`, `suppress_weeks`
- `rejection_reasons.yaml` annotated with `permanent` and `suppress_weeks`
- `GET /api/meal-plan/shopping-list` — Phase 5 pipeline
- `GET /api/meal-plan/pantry-review/{id}` — weekly pantry snapshot

### Changed
- Matching engine outputs flat ranked list (not day-slotted)
- Temporary rejections auto-expire; catalog keeps growing

## [0.3.0] — Phase 3 — 2026-06-22

### Added
- Matching engine with pantry overlap, liked/disliked/excluded scoring
- Mealie tag filtering (`mealie_dinner_tag` preference)
- Weekly intent hints with per-week score boost
- Two-pool suggestion mix (favourites + discovery)
- `GET /api/meal-plan/suggest`

## [0.2.0] — Phase 2 — 2026-06-21

### Added
- Mealie recipe import (`POST /api/recipes/import`)
- Recipe rejection with reason categories
- Weekly review / favourites loop (`POST /api/meal-plan/entries/{id}/review`)
- Mealie rating sync on favourite

## [0.1.0] — Phase 1 — 2026-06-21

### Added
- Household + preferences CRUD
- Pantry items + staples CRUD
- Docker Compose + PostgreSQL
- YAML config files (pantry_staples, package_sizes, rejection_reasons, cooking_vocabulary)
