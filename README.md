# Recipe Planner

Self-hosted meal planning app: tracks on-hand pantry ingredients, scrapes
trusted recipe sites for matches, generates weekly shopping lists, and
exports recipes to PDF. Built to run on Unraid via Docker Compose.

## Status: Phase 1 — Pantry CRUD (this commit)

Working: household profile + pantry item CRUD via a FastAPI backend and
Postgres database.

### Roadmap
1. ✅ Data model + pantry CRUD
2. ⬜ Recipe scraper module (`recipe-scrapers` + curated site allowlist) — populates the `recipes` table beyond the current stub
3. ⬜ Matching engine (pantry overlap %, rating threshold, hard excludes from preferences, rejection history)
4. ✅ Interview flow (servings, diet, cuisine, cook-time, skill, methods, cookware — all editable later)
5. ⬜ Weekly plan generator → shopping list → Apple Reminders export, PDF recipe export

## Security architecture

This app has **no login/auth layer** — it's designed for trusted local
network access only via a web browser. To keep that safe without
credentials, the Docker setup is hardened instead:

- **Isolated network** (`recipe-planner-net`) — the `db` and `api`
  containers can only reach each other. They cannot see, and cannot be
  seen by, any other container on your Unraid box.
- **No database port exposure** — Postgres has no host port mapping at all;
  only the `api` container can reach it, over the internal network.
- **Single exposed port** — `8000` (the API/web UI) is the only port
  reachable from your LAN. **Do not** forward it through your router or
  otherwise expose it to the internet — there's no auth to stop anyone who
  can reach it.
- **Non-root container user**, dropped Linux capabilities, and
  `no-new-privileges` on both containers, limiting blast radius if a
  dependency or a scraped page tries something malicious.
- **Outbound internet access is still needed** by the `api` container — for
  scraping recipe sites and calling your Mealie instance — so this isn't a
  fully air-gapped sandbox, but it is isolated from your other containers
  and not reachable from outside your LAN.

One practical note: the `./backend/app:/app/app` bind mount (used for live
code reload) takes on the host directory's file ownership, which can
conflict with the non-root container user when the API writes to the YAML
config files. If you hit a permission error writing staples via the API,
run `chmod -R a+rwX backend/app/data` on the Unraid host.

## Mealie integration

Recipe storage is delegated to a self-hosted [Mealie](https://mealie.io)
instance rather than reinventing recipe storage/scraping — Mealie already
has a robust URL-import scraper, ratings, tags, and favorites. This app
becomes the layer on top: pantry tracking, preferences, weekly planning
logic, and the favorites/feedback loop described below, talking to Mealie
over its REST API using a service token (`MEALIE_API_TOKEN` in `.env`,
backend-only, never exposed to the browser).

## Weekly review & favorites loop

Each week, the app:
1. Reviews the previous week's planned recipes and any feedback recorded
   for them.
2. Recipes with favorable feedback get persisted as long-term "favorites"
   (stored/tagged in Mealie).
3. The next week's recipe suggestions are always a deliberate **mix** of
   previously-favorited recipes and newly discovered, highly-rated recipes
   from outside sources — never 100% repeats, never 100% untested new
   recipes.

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
   `backend/app/data/package_sizes.yaml` as the reference table. This table
   is intentionally small to start and expands as new ingredients are
   encountered.
4. **Surplus carry-forward** — when a rounded purchase exceeds what's needed
   that week, the leftover is written back into the pantry as expected
   on-hand stock for next week's planning, closing the waste-reduction loop.

## Hand-editable config files

Several pieces of configuration are intentionally stored as plain YAML
files under `backend/app/data/`, not just in the database, so they're easy
to open and edit directly — in VS Code, through the bind-mounted
`backend/app` directory, or over a network share on Unraid:

| File | Purpose |
|---|---|
| `pantry_staples.yaml` | Always-on-hand ingredients (salt, oil, flour...). Never appear on a shopping list. |
| `cooking_vocabulary.yaml` | Valid skill levels, cooking methods, and cookware — drives interview options. |
| `package_sizes.yaml` | Real-world purchase units used to round shopping list quantities (Phase 5). |
| `rejection_reasons.yaml` | Controlled categories for why a recipe was rejected. |

These are read fresh on every API request (no caching, no restart needed),
and the API endpoints that write to them (e.g. `POST /pantry/staples`) use
a comment-preserving YAML writer, so hand-added comments survive even after
the app edits the same file.

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

# View / add to the always-on-hand staples list (file-backed)
curl http://localhost:8000/pantry/staples
curl -X POST http://localhost:8000/pantry/staples -H "Content-Type: application/json" -d '{"name": "soy sauce"}'

# Create a recipe stub (Phase 2 scraper will populate full details later)
curl -X POST http://localhost:8000/recipes \
  -H "Content-Type: application/json" \
  -d '{"source_url": "https://www.seriouseats.com/example-recipe", "title": "Example Recipe"}'

# See valid rejection reason categories
curl http://localhost:8000/recipes/rejection-reasons

# Reject a recipe option with a reason
curl -X POST http://localhost:8000/recipes/<recipe_id>/reject \
  -H "Content-Type: application/json" \
  -d '{"household_id": "<id>", "reason_category": "cook_method_unavailable", "reason_detail": "Requires a smoker, we don'\''t have one"}'
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
  `GET /preferences/vocabulary` / `backend/app/data/cooking_vocabulary.yaml`,
  which is meant to be extended over time (e.g. a new appliance) without a
  schema change.
- **`recipe_options_per_meal`** — how many candidate recipes the planner
  offers per meal slot (default 3), rather than auto-picking one. Each
  option can be individually rejected with a reason (see below).
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
