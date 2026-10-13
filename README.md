# Recipe Planner

Self-hosted household dinner meal planner. Plans weekly dinners using on-hand pantry ingredients, minimises food waste, and builds a personalised recipe catalog that improves every week through a favourites and rejection feedback loop.

**Runs on:** `http://<unraid-ip>:8111`  
**API docs:** `http://<unraid-ip>:8111/docs`  
**Mobile:** fully responsive — hamburger drawer nav on screens ≤ 639 px  
**Timezone:** set `TZ` in the Unraid template to match your local timezone for correct week calculation and scrape schedule  
**Version:** 139

---

## Architecture

| Layer | Technology |
|---|---|
| Backend | Python / FastAPI |
| Database | SQLite (WAL mode) |
| Recipe storage | Self-hosted [Mealie](https://mealie.io) |
| Recipe discovery | HelloFresh, Home Chef, Budget Bytes, Pinch of Yum + Mealie library |
| Shopping list push | [Bring!](https://www.getbring.com) and/or Home Assistant todo list (optional) |
| Frontend | React SPA (single HTML file, served by FastAPI) |
| Deployment | Docker on Unraid, port `8111` |

---

## How it works

### Recipe discovery & sources

Recipes come from multiple sources, all configurable via the **Sources page** in the UI:

- **HelloFresh** — A–Z recipe directory pages
- **Home Chef** — Dinner-focused category pages across 13 categories
- **Budget Bytes** — Main dish, soup, pasta, bowl-meals categories. Article-tag link extraction, cost annotations (`$5.66*`) stripped from ingredients automatically
- **Pinch of Yum** — Dinner, chicken, beef-and-pork, pasta, soups-and-stews, seafood
- **Mealie library** — All 154+ recipes in your local Mealie instance are synced nightly and available in search and suggestions

A **nightly background scraper** (`scrape_job.py`) runs at 3 AM (configurable) to keep the recipe cache warm and sync the Mealie library. When you hit "Generate Suggestions" the engine scores from the cache — no network wait.

Additional sources can be added via the Sources page. Sources that block scraping (EatingWell, Serious Eats, The Kitchn) are excluded.

### Finding new recipe sources (site vetter)

`tools/site_vetter/run.sh` tests every recipe-scrapers-supported site
against the app's own discovery and scraping code and writes
`tools/site_vetter/out/report.md` (verdicts + family-dinner fit score) and
`suggested_sources.yaml` (paste-ready `recipe_sources.yaml` entries).
Run `./run.sh` for all US-region sites (~12 min), `./run.sh --hosts a.com,b.com`, or
`./run.sh --merge --hosts a.com` to re-vet a few sites into the existing results.

### Suggestion engine

Every suggestions load combines:

**Scored picks** — recipes ranked by pantry overlap, ratings, cook time, and rejection history. Grouped by protein category.

**Random picks** — 10 random recipes per protein category appended below a dashed divider. Gives full catalogue visibility beyond the top-scored items. Count configurable in Settings (0 to disable).

**Pre-selected recipes** — any recipes added via Recipe Search or Past Meals for the chosen week appear at the top of the suggestions page under "Added to this week", already checked off.

### Weekly planning flow

```
0. Select Week        — pick current, next, or upcoming week
1. Pantry Review      — full pantry view with add/edit inline; set the number
                        of suggestions and press Generate Suggestions
2. Suggestions        — opens immediately; scored picks load first (~2 s), then
                        random picks append; grouped by protein category;
                        pre-selected recipes shown at top; search/filter;
                        ingredient actions (staple / exclude / dislike)
3. Confirm            — adjust servings per recipe; Print All scales ingredients;
                        "Lock In & Import →" imports to Mealie and advances
4. Shopping List      — pantry-first, package-rounded, section-grouped list;
                        push to Bring! and/or Home Assistant in one click;
                        pantry-check items promotable to buy list
```

### Kitchen Display

`http://<server>:8111/kitchen` is a full-screen, touch-friendly view of the
week's confirmed dinners for a kitchen tablet. Cards show the Mealie photo,
time and servings, and open the recipe in Mealie. Buttons switch between this
week and next and jump to Mealie or the Planner. It refreshes every 10 minutes
and follows the calendar; `?week=YYYY-MM-DD` pins a week.
Extra header buttons (e.g. Pinterest) are edited in **Settings → Kitchen Display**
and stored in `backend/app/data/kitchen.yaml`.

### Recipe Search

- Searches all ~700+ local recipe stubs including Mealie library
- Result count selector (10 / 25 / 50 / 100 / 200)
- Week picker — add any result to current or upcoming weeks
- Servings override before adding to plan
- Ingredient actions: add to staples, exclude, dislike

### Past Meals

- All previously selected recipes grouped by week
- One-click re-add to any week via week picker
- Rate or reject from history

---

## Scoring

| Signal | Points |
|---|---|
| Pantry overlap (% of ingredients on hand + staples) | 0 – 50 |
| Weekly intent hints (API only; no longer set by the planner) | +15 each, max +45 |
| Long-term liked items | +5 each, max +20 |
| Soft disliked items | −15 each, max −45 |
| Cook time over household max | −20 |
| Favourite bonus (previously rated ≥ threshold) | +25 |
| Dinner-tag bonus (Mealie `dinner-planner` tag) | +10 |

---

## Rejection & suppression

Recipes are excluded from scored suggestions if they are:

- Permanently rejected by this household
- Temporarily rejected and suppression window not expired

Rejected recipes are also excluded from the random pick pool.

| Category | Type | Suppressed for |
|---|---|---|
| `not_this_week` | Temporary | 2 weeks |
| `already_made_recently` | Temporary | 3 weeks |
| `too_time_consuming` | Temporary | 2 weeks |
| `too_expensive` | Temporary | 2 weeks |
| `too_complex` | Temporary | 2 weeks |
| `missing_key_ingredient` | Temporary | 2 weeks |
| `wrong_meal_type` | Permanent | never resurface |
| `not_a_dinner` | Permanent | never resurface |
| `side_dish` | Permanent | never resurface |
| `too_many_servings` | Permanent | never resurface |
| `not_applicable` | Permanent | never resurface |
| `too_heavy` | Temporary | 2 weeks |
| `already_planned` | Temporary | 1 week |
| `other` | Temporary | 1 week |
| `dislike` | Permanent | never resurface |
| `allergy` | Permanent | never resurface |
| `disliked_ingredient` | Permanent | never resurface |
| `cook_method_unavailable` | Permanent | never resurface |
| `cookware_unavailable` | Permanent | never resurface |
| `not_applicable` | Permanent | never resurface |
| `side_dish` | Permanent | never resurface |

---

## Shopping list

- **Pantry-first** — on-hand stock depleted across all selected recipes before anything hits the list
- **Aggregate then round** — total need summed across all recipes, rounded up to a real package size
- **Staples never bought** — salt, pepper, oil, etc. assumed always on hand
- **Bring! push** — sends every BUY item + pantry-promoted items to Bring! grocery list
- **Home Assistant push** — optionally sends to an HA todo list entity
- **Add from pantry** — pantry-check items promoted to buy list are included in push
- **Print All** — scales ingredient quantities to selected servings per recipe

---

## Settings & preferences

Accessible via the gear icon. Key preferences:

| Setting | Default | Description |
|---|---|---|
| Household size | 4 | Used as default servings for all recipes |
| Default suggestions | 10 | Number of scored picks per suggestions load |
| Random picks per category | 10 | Random pool appended per protein category; 0 to disable |
| Max cook time | — | Recipes over limit get a score penalty |
| Bring! enabled | true | Enable/disable Bring! shopping list push |
| HA shopping enabled | false | Enable/disable Home Assistant todo list push |
| Ollama normalise | false | Use local Ollama LLM for ingredient normalisation |

---

## Database page (Tools menu)

- Per-source recipe stub counts
- **🔄 Sync from Mealie** — pull latest Mealie library into local DB on demand
- **↺ Refresh** — reload stub counts
- **🧹 Remove Duplicates** — dedup recipes with same title (keeps most complete stub)
- Wipe stubs per source (with confirmation)

---

## Running on Unraid

### Option A — Unraid Community Applications template (recommended)

The template is at `unraid-template/my-Recipe-Planner.xml`. Copy it to `/boot/config/plugins/dockerMan/templates-user/` then **Docker → Add Container → recipe-planner**.

**Volume mounts:**

| Container path | Host path | Purpose |
|---|---|---|
| `/app/data` | `.../data/db` | SQLite database — back up before updating |
| `/app/app/data` | `.../backend/app/data` | YAML config files (sources, staples, etc.) |
| `/app/app/static` | `.../backend/app/static` | Frontend HTML — enables updates without image rebuild |

> The static files mount is important — without it, frontend changes require a full image rebuild and `docker pull`.

### Option B — git + docker-compose (development)

```bash
git clone https://github.com/rgabrielson11/recipe-planner.git /mnt/user/appdata/recipe-planner
cd /mnt/user/appdata/recipe-planner
cp .env.example .env   # fill in MEALIE_BASE_URL, MEALIE_API_TOKEN, etc.
bash deploy.sh         # backs up DB + YAML, pulls, rebuilds, restarts
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MEALIE_BASE_URL` | Yes | Your Mealie instance URL, e.g. `http://192.168.1.x:9100` |
| `MEALIE_API_TOKEN` | Yes | Mealie service account API token |
| `DATABASE_PATH` | No | SQLite file path (default `/app/data/recipe_planner.db`) |
| `BRING_EMAIL` | No | Bring! account email |
| `BRING_PASSWORD` | No | Bring! account password |
| `RECIPE_PLANNER_URL` | No | LAN URL for Homepage dashboard label |
| `OLLAMA_BASE_URL` | No | Local Ollama URL for ingredient normalisation |
| `OLLAMA_MODEL` | No | Ollama model (default `qwen3:latest`) |
| `OLLAMA_TIMEOUT` | No | Ollama timeout in seconds (default `30`) |
| `TZ` | No | Container timezone (default `America/Los_Angeles`) |

---

## Configuration files

All files are bind-mounted and read fresh on every request — no restart needed after edits.

| File | Purpose |
|---|---|
| `backend/app/data/pantry_staples.yaml` | Ingredients always assumed on hand (never on shopping list) |
| `backend/app/data/package_sizes.yaml` | Real retail package sizes for shopping list rounding |
| `backend/app/data/rejection_reasons.yaml` | Rejection vocabulary, labels, and permanence flags |
| `backend/app/data/cooking_vocabulary.yaml` | Cooking methods, cookware, and skill levels for preferences UI |
| `backend/app/data/recipe_sources.yaml` | Discovery sources, scrape budget, non-dinner keyword filter, nightly job schedule |

### Key `recipe_sources.yaml` settings

```yaml
discovery:
  max_scraped_per_run: 60
  request_delay_seconds: 1.5
  stub_rescrape_days: 7
  min_scraped_rating: 4.0     # quality gate where a page publishes ratings
  min_scraped_reviews: 10     # min review count where published (blogs get far fewer than meal kits)
  background_scrape_enabled: true
  background_scrape_hour: 3     # 0-23, server local time
  non_dinner_title_keywords:
    - cheesecake
    - pancake
    - waffle
```

---

## Security

No login/auth — designed for trusted LAN access only. Do **not** forward port 8111 through your router.

- Non-root container, dropped Linux capabilities, `no-new-privileges`
- SSRF protection on recipe import (`url_safety.py`)
- XSS-safe print views (HTML escaped before `document.write`)
- Mealie query-filter injection protection
