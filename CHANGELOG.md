# Recipe Planner — Changelog

## Phase 9 — Patch 2: fix Mealie PATCH full-body requirement

### Bug fix

**`mealie_client.py` — 422 on tag and cost-strip PATCH calls**

Mealie's `PATCH /api/recipes/{slug}` has PUT semantics: it replaces the entire
recipe with the body sent.  Two places were sending partial payloads:

- `add_tag_to_recipe` sent `{"tags": […]}` — caused `422 Unprocessable Entity`
  because required recipe fields were absent.  Now sends the full recipe detail
  dict (from `get_recipe()`) with the `tags` key updated in-place.

- The cost-strip step in `import_recipe_from_url` sent
  `{"recipeIngredient": […]}` — same issue.  Now sends the full `cleaned`
  detail dict.



## Phase 9 — Patch: additional rejection reasons

### Changes
- **`rejection_reasons.yaml`** — two new permanent rejection reasons added:
  - `not_applicable` — "Not applicable for dinner (e.g. dessert, breakfast, snack)"
  - `side_dish` — "Side dish — not a main course"
  
  Both are `permanent: true` so rejected recipes are never surfaced again.
  No code change required — the YAML file is the single source of truth for the rejection vocabulary.



## Phase 7 — Suggestion fixes, quality gate, and live log viewer

### Bug fixes

**Critical: suggestions empty after first run (Pool B stub loop)**
The discovery engine previously added ALL Recipe table URLs to `known_urls`,
which excluded stubs created in earlier runs from future scraping.  After the
first discovery run, Pool B (new recipes) would return nothing because every
URL it had ever seen was already in the DB.

Fix: `known_urls` now contains only Mealie-imported URLs (recipes with a
`mealie_slug`).  Stubs (in DB, not yet in Mealie) are queued for re-scraping
each week in Pool X (up to `max_scrape // 2` per run), ensuring they are
always scored and eligible for suggestion.

**Mealie dinner-tag hard filter removed (issue 1 — featured items restrict too much)**
Pool A previously hard-filtered to only Mealie recipes tagged with
`mealie_dinner_tag`.  If no recipes in Mealie carried that tag, Pool A was
always empty.  The filter is now a soft **+10 pt score boost** — tagged
recipes are prioritised but un-tagged ones are still eligible.

**Pool B discovery slot calculation fixed**
`discovery_slots` was computed as `n - mealie_fav_cnt` (a constant), which
could cap Pool B even when Pool A delivered fewer than `mealie_fav_cnt`
results.  Pool B now always fills `n - len(actual_mealie_favs)` slots.

### New features

**Live log viewer — GET /api/logs**
All application log records (DEBUG level and above) are captured in a
1 000-entry in-memory ring buffer.  The `/api/logs` endpoint exposes them
without requiring Docker log access:

```
GET /api/logs                              # last 200 records, all levels
GET /api/logs?level=WARNING                # warnings + errors only
GET /api/logs?logger_filter=discovery      # filter by module name
GET /api/logs?level=DEBUG&last_n=500       # last 500 debug lines
```

Docker logs (`docker logs recipe-planner-backend`) also remain active.

**Recipe quality gate for scraped recipes (issue 4)**
Newly scraped recipes from community-rated sites (AllRecipes, Food Network,
Skinnytaste…) are now subject to a quality gate:

  - `min_scraped_rating: 4.0`   — reject recipes rated below 4 stars
  - `min_scraped_reviews: 100`  — reject recipes with fewer than 100 ratings

Both thresholds are configurable in `recipe_sources.yaml`.

Editorial / blog sources (Serious Eats, Budget Bytes, Half Baked Harvest,
Simply Recipes, Cookie and Kate, etc.) do not embed structured rating data in
their markup, so the gate does not apply to them — all their recipes pass
through to the scoring engine as before.

### Configuration changes

`backend/app/data/recipe_sources.yaml` — two new keys under `discovery:`:

```yaml
min_scraped_rating: 4.0     # stars, applies when structured data present
min_scraped_reviews: 100    # review count, applies when structured data present
```

Set both to `0` to disable the quality gate entirely.

---

## Phase 6b — Recipe discovery + Mealie integration

- Pool A/B matching engine
- dinner_tag support
- Shopping list PDF/Apple Reminders export

## Phase 5 — Shopping list engine

## Phase 4 — Weekly selection + review workflow

## Phase 3 — Matching engine skeleton

## Phase 2 — Pantry tracking + household preferences

## Phase 1 — Project scaffold
