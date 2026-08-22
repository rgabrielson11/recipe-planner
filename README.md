# Recipe Planner

Self-hosted household dinner meal planner for a family of 4. Plans weekly dinners using on-hand pantry ingredients, minimises food waste, and builds a personalised recipe catalog that improves every week through a favourites and rejection feedback loop.

**Runs on:** `http://<unraid-ip>:8111`  
**API docs:** `http://<unraid-ip>:8111/docs`

---

## Architecture

| Layer | Technology |
|---|---|
| Backend | Python / FastAPI |
| Database | SQLite (WAL mode) |
| Recipe storage | Self-hosted [Mealie](https://mealie.io) |
| Recipe discovery | HelloFresh A–Z directory + Home Chef category pages |
| Shopping list push | [Bring!](https://www.getbring.com) (optional) |
| Frontend | React SPA (single HTML file, served by FastAPI) |
| Deployment | Docker on Unraid, port `8111` |

---

## How it works

### Recipe discovery

Recipes come from two sources:

**HelloFresh** — A–Z recipe directory pages (`/pages/sitemap/recipes-a` … `z`). Individual recipe URLs end in a 24-char hex ID which the engine uses to distinguish real recipes from hub/category pages.

**Home Chef** — Dinner-focused category pages (`/recipes/chicken`, `/recipes/beef`, `/recipes/pork`, `/recipes/seafood`, etc. — 32 pages across 13 categories). Individual meals live at `/meals/{slug}`.

A **nightly background scraper** (`scrape_job.py`) runs at 3 AM (configurable) to keep the recipe cache warm. When you hit "Generate Suggestions" the engine scores from the cache — no network wait. The first run after deploy triggers a one-time synchronous scrape.

A **non-dinner keyword filter** screens candidate URLs by slug before scraping — pancakes, cheesecakes, smoothies, and similar non-dinner content never waste scrape budget. Edit `non_dinner_title_keywords` in `recipe_sources.yaml` to tune.

### Suggestion engine

Two recipe pools are combined every week:

**Pool A — Mealie proven favourites** (default 2 slots)  
Recipes in your Mealie library rated ≥ `mealie_min_rating` (default 4★). Tagged with `dinner-planner` get a +10 soft boost; all high-rated recipes are still eligible regardless of tag.

**Pool B — Discovered recipes** (fills remaining slots)  
Scraped from HelloFresh and Home Chef, scored against your current pantry, and ranked. These are imported to Mealie automatically when you confirm selections.

### Weekly workflow

```
1. Review pantry          — update what's on hand
2. Set weekly intent      — ingredient hints boost matching scores
3. Generate suggestions   — ranked list of N recipes
4. Accept / reject        — permanent or time-based suppression
5. Confirm selections     — locks in your picks for the week
6. Get shopping list      — pantry-first, package-rounded, section-grouped
7. Push to Bring!         — optional one-tap push to the Bring! grocery app
8. Rate meals (end of week) — 4★+ → favourite, boosted in future suggestions
```

---

## Scoring

| Signal | Points |
|---|---|
| Pantry overlap (% of ingredients on hand + staples) | 0 – 50 |
| Weekly intent hints | +15 each, max +45 |
| Long-term liked items | +5 each, max +20 |
| Soft disliked items | −15 each, max −45 |
| Cook time over household max | −20 |
| Favourite bonus (Pool A, previously rated ≥ threshold) | +25 |
| Dinner-tag bonus (Mealie `dinner-planner` tag) | +10 |

**Hard filters** — recipe never shown if:
- Excluded ingredient in recipe text
- Required cooking method not in `available_methods`
- Permanently rejected by this household
- Temporarily rejected and suppression window not expired

---

## Rejection reasons

| Category | Type | Suppressed for |
|---|---|---|
| `not_this_week` | Temporary | 2 weeks |
| `already_made_recently` | Temporary | 3 weeks |
| `too_time_consuming` | Temporary | 2 weeks |
| `too_expensive` | Temporary | 2 weeks |
| `too_complex` | Temporary | 2 weeks |
| `missing_key_ingredient` | Temporary | 2 weeks |
| `other` | Temporary | 1 week |
| `dislike` | Permanent | never resurface |
| `allergy` | Permanent | never resurface |
| `disliked_ingredient` | Permanent | never resurface |
| `cook_method_unavailable` | Permanent | never resurface |
| `cookware_unavailable` | Permanent | never resurface |
| `not_applicable` | Permanent | never resurface |
| `side_dish` | Permanent | never resurface |

Full list with labels: `GET /recipes/rejection-reasons`  
Edit vocabulary: `backend/app/data/rejection_reasons.yaml`

---

## Shopping list

- **Pantry-first** — on-hand stock depleted across all selected recipes before anything hits the list
- **Aggregate then round** — total need summed across all recipes, rounded up once to a real package size
- **Staples never bought** — salt, pepper, oil, etc. assumed always on hand
- **Bring! push** — `POST /meal-plan/shopping-list/push-to-bring` sends every BUY item to your Bring! grocery list

---

## Running on Unraid

### Option A — git + docker-compose (recommended for development)

```bash
git clone https://github.com/rgabrielson11/recipe-planner.git /mnt/user/appdata/recipe-planner
cd /mnt/user/appdata/recipe-planner
cp .env.example .env          # fill in MEALIE_BASE_URL, MEALIE_API_TOKEN, etc.
bash deploy.sh                 # backs up DB + YAML, pulls, rebuilds, restarts
```

To update: `bash deploy.sh` (or `git pull && docker compose up -d --build`).

If you hit file-permission errors on YAML configs:
```bash
chmod -R a+rwX backend/app/data
```

### Option B — Unraid Community Applications template

A published Docker image is built and pushed to `ghcr.io/rgabrielson11/recipe-planner:latest` on every push to `master`. The template is at `unraid-template/my-Recipe-Planner.xml` — copy it to `/boot/config/plugins/dockerMan/templates-user/` on your Unraid box, then **Docker → Add Container → pick "recipe-planner"**.

Code updates happen by pulling a new image (**Docker tab → Check for Updates**) rather than editing files. Only the database and YAML config directory are persisted.

> First time only: the GHCR package defaults to **private**. Go to GitHub → Packages → recipe-planner → Package settings → change visibility to Public, or `docker login ghcr.io` with a PAT that has `read:packages` scope.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MEALIE_BASE_URL` | Yes | Your Mealie instance URL, e.g. `http://192.168.1.x:9100` |
| `MEALIE_API_TOKEN` | Yes | Mealie service account API token |
| `DATABASE_PATH` | No | SQLite file path (default `/app/data/recipe_planner.db`) |
| `BRING_EMAIL` | No | Bring! account email — leave blank to disable |
| `BRING_PASSWORD` | No | Bring! account password |
| `RECIPE_PLANNER_URL` | No | LAN URL for Homepage dashboard label, e.g. `http://192.168.1.x:8111` |

---

## Configuration files

All files are bind-mounted and read fresh on every request — no restart needed after edits.

| File | Purpose |
|---|---|
| `backend/app/data/pantry_staples.yaml` | Ingredients always assumed on hand (never on shopping list) |
| `backend/app/data/package_sizes.yaml` | Real retail package sizes for shopping list rounding |
| `backend/app/data/rejection_reasons.yaml` | Rejection vocabulary, labels, and permanence flags |
| `backend/app/data/cooking_vocabulary.yaml` | Cooking methods, cookware, and skill levels for preferences UI |
| `backend/app/data/recipe_sources.yaml` | Discovery sources (HelloFresh + Home Chef), scrape budget, non-dinner keyword filter, nightly job schedule |

### Key `recipe_sources.yaml` settings

```yaml
discovery:
  max_scraped_per_run: 60        # scrape budget per run
  request_delay_seconds: 1.5    # polite pause between requests
  stub_rescrape_days: 7         # re-scrape stubs older than this
  background_scrape_enabled: true
  background_scrape_hour: 3     # 0-23, server local time
  non_dinner_title_keywords:    # URL slug pre-filter
    - cheesecake
    - pancake
    - waffle
    # … add more to tune
```

---

## Homepage dashboard integration

The container exposes [Homepage](https://gethomepage.dev) auto-discovery labels:

```yaml
# In your Homepage services.yaml — or rely on Docker auto-discovery:
- Kitchen:
    - Recipe Planner:
        container: recipe-planner
```

The label uses `mdi-silverware-fork-knife` (built into Homepage's MDI icon pack).

---

## Security

No login/auth — designed for trusted LAN access only. Do **not** forward port 8111 through your router.

- Non-root container, dropped Linux capabilities, `no-new-privileges`
- SSRF protection on recipe import (`url_safety.py` — rejects private/loopback IPs)
- XSS-safe print views (HTML escaped before `document.write`)
- Mealie query-filter injection protection (`_escape_filter_value()`)
