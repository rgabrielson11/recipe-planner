# Recipe Planner

Professional household dinner meal planner for a family of 4.

**Mission:** plan dinner meals using a combination of on-hand ingredients and
weekly shopping trips — minimising food waste, sourcing recipes from credible
sites via Mealie, and building a personalised dinner catalog that improves
every week through a favorites and rejection feedback loop.

**Runs on:** `http://<unraid-ip>:8111`  
**Interactive docs:** `http://<unraid-ip>:8111/docs`  
**Weekly workflow reference:** `GET /workflow`

---

## Status

| Phase | Description | Status |
|---|---|---|
| 1 | Data models + pantry CRUD | ✅ |
| 2 | Mealie recipe import + local reference rows | ✅ |
| 3 | Matching engine — flat ranked suggestions | ✅ |
| 4 | Weekly intent, selections, two-tier rejection | ✅ |
| 5 | Shopping list generation (pantry-first, package rounding) | ✅ |
| — | Apple Reminders export | ⬜ |
| — | PDF shopping list / recipe export | ⬜ |
| — | Curated-site allowlist for bulk recipe discovery | ⬜ |

---

## Weekly planning workflow

```
STEP 1 — Review pantry
  GET /meal-plan/pantry-review/{household_id}
  → Categorised on-hand snapshot with expiry flags.
    Update via PATCH /pantry/{item_id} and POST /pantry.

STEP 2 — Set this week's intent
  POST /meal-plan/week-intent/{household_id}/{week_start_date}
  {
    "ingredient_hints":  ["chicken thighs", "salmon", "bbq"],
    "num_suggestions":   10,
    "pantry_snapshot_notes": "salmon in freezer, half bag of rice"
  }
  → num_suggestions varies week to week.
    Hints boost matched recipe scores (+15 pts each, max +45).

STEP 3 — Get suggestions
  GET /meal-plan/suggest?household_id=...&week_start_date=...
  → Flat ranked list of N recipes. Each shows: score, pantry_overlap_pct,
    missing_ingredients, total_time_minutes, is_favorite.

STEP 4 — Handle the ones you're skipping (optional but valuable)
  POST /recipes/{recipe_id}/reject
  { "household_id": "...", "reason_category": "not_this_week" }

  Two tiers of rejection:
  ┌──────────────────────────────┬───────────────────────────────────────────┐
  │ PERMANENT (never resurface)  │ dislike, disliked_ingredient, allergy,    │
  │                              │ cook_method_unavailable, cookware_unavail  │
  ├──────────────────────────────┼───────────────────────────────────────────┤
  │ TEMPORARY (suppress N weeks) │ not_this_week (2 wks)                     │
  │                              │ already_made_recently (3 wks)             │
  │                              │ too_time_consuming (2 wks)                │
  │                              │ too_expensive (2 wks)                     │
  │                              │ too_complex (2 wks)                       │
  │                              │ missing_key_ingredient (2 wks)            │
  │                              │ other (1 wk)                              │
  └──────────────────────────────┴───────────────────────────────────────────┘
  Temporary rejections resurface automatically — the catalog keeps growing.
  GET /recipes/rejection-reasons for the full list with suppress_weeks.

STEP 5 — Lock in your selections
  POST /meal-plan/selections
  {
    "household_id":    "...",
    "week_start_date": "2026-06-23",
    "recipe_ids":      ["id-a", "id-b", "id-c"]
  }
  → Creates MealPlanEntry rows so end-of-week rating works.
    You suggested 10, you pick 3 — only the 3 go to the shopping list.

STEP 6 — Get the shopping list
  GET /meal-plan/shopping-list?household_id=...&week_start_date=...
  → Built ONLY from your confirmed selections.
    Pipeline: scale → aggregate → subtract pantry → subtract staples
              → round to package sizes → group by store section.

STEP 7 — Rate meals at end of week
  POST /meal-plan/entries/{entry_id}/review  { "rating": 4 }
  → ≥ favorite_rating_threshold (default 4★) → marked favorite,
    synced to Mealie. Favorites get a +25 pt boost in future suggestions.
```

---

## Matching engine scoring

| Signal | Points |
|---|---|
| Pantry overlap (% of ingredients on hand + staples) | 0 – 50 |
| **Weekly intent hints** (this-week keywords) | **+15 each, max +45** |
| Long-term liked items / cuisines | +5 each, max +20 |
| Soft disliked items | −15 each, max −45 |
| Cook time over household max | −20 (soft) |
| Favorite bonus (previously rated ≥ threshold) | +25 |

**Hard filters (recipe never shown):**
- Excluded item (allergy / never-make) in recipe text
- Required cooking method not in available_methods
- Permanently rejected by this household
- Temporarily rejected AND suppression window not yet expired

---

## Mealie tag filtering

Only Mealie recipes carrying the `dinner-planner` tag (configurable via
`mealie_dinner_tag` in preferences) appear in suggestions. This keeps
baking, breakfast, and other non-dinner recipes out of the weekly pool.

**Setup:** in Mealie, add the tag `dinner-planner` to every recipe you want
included in dinner planning. The engine passes it as a server-side
`queryFilter` and double-checks client-side as a fallback.

Change the tag: `PUT /preferences/{household_id}` → `{"mealie_dinner_tag": "my-tag"}`  
Disable filtering: set `mealie_dinner_tag` to `""`

---

## Shopping list design

- **Pantry-first** — on-hand stock is depleted across all selected recipes
  before anything hits the list
- **Aggregate before rounding** — total need is summed across all recipes,
  then rounded up once to a real package size — not per-recipe
- **Real package sizes** — defined in `backend/app/data/package_sizes.yaml`;
  hand-editable, no code change needed to add new items
- **Staples never bought** — salt, pepper, oil, etc. are assumed always on
  hand; edit `backend/app/data/pantry_staples.yaml`
- **Unit-mismatch warnings** — if pantry has "1 lb chicken" and recipe needs
  "2 pieces chicken" the engine flags it rather than silently skipping

---

## Security

No login/auth — designed for trusted LAN access only.

- **Isolated network** (`recipe-planner-net`) — `db` and `api` containers
  only reach each other; invisible to all other Unraid containers
- **No database port exposure** — Postgres has no host port mapping
- **Single exposed port** — `8111` only. Do not forward through your router
- **Non-root containers**, dropped Linux capabilities, `no-new-privileges`

---

## Running on Unraid

Two ways to run this, pick whichever fits how you want to maintain it.

### Option A — git + docker-compose (source checkout, live code)

This is the original setup and what `deploy.sh` automates: the repo is
checked out directly on the box and `backend/app` is bind-mounted into
the container, so it's straightforward to patch code in place.

```bash
git clone git@github.com:rgabrielson11/recipe-planner.git /mnt/user/appdata/recipe-planner
cd /mnt/user/appdata/recipe-planner
cp .env.example .env   # fill in MEALIE_BASE_URL, MEALIE_API_TOKEN, etc.
docker compose up -d --build
```

To update: `git pull` (or run `deploy.sh`, which also backs up the DB and
YAML config first), then `docker compose up -d --build`.

If you hit file-permission errors writing to YAML configs:
```bash
chmod -R a+rwX backend/app/data
```

### Option B — Unraid template (published image, GUI-managed)

`.github/workflows/docker-build.yml` builds and pushes an image to
`ghcr.io/rgabrielson11/recipe-planner:latest` on every push to `master`.
`unraid-template/my-Recipe-Planner.xml` is a ready-made Unraid Community
Applications-style template for it — copy it to
`/boot/config/plugins/dockerMan/templates-user/` on your Unraid box (or
it's already there if you set this up with Claude), then Docker -> Add
Container -> pick "recipe-planner" from the Template dropdown.

Unlike Option A, this does **not** bind-mount live source code — it runs
whatever's baked into the image, so code updates happen by pulling a new
image (Docker tab -> Check for Updates) rather than editing files on the
box. Only the database and the YAML config directory are persisted.
First time only: the GHCR package defaults to **private** — go to
github.com/rgabrielson11?tab=packages -> recipe-planner -> Package
settings -> change visibility to Public, or `docker login ghcr.io` on the
Unraid host with a PAT that has `read:packages` scope.

API: `http://<unraid-ip>:8111`  
Docs: `http://<unraid-ip>:8111/docs`

---

## Configuration files (hand-editable, no restart needed)

| File | Purpose |
|---|---|
| `backend/app/data/pantry_staples.yaml` | Ingredients always assumed on hand |
| `backend/app/data/package_sizes.yaml` | Real retail package sizes for rounding |
| `backend/app/data/rejection_reasons.yaml` | Rejection vocabulary + permanence flags |
| `backend/app/data/cooking_vocabulary.yaml` | Cooking methods, cookware, skill levels |
