# Recipe Planner — Changelog

## Phase 10 — Patch 26: strip parenthetical units from ingredients

### Bug fix

`_ingredient_names_from_text()` now strips parenthetical unit annotations
that appear anywhere in the ingredient string, e.g.:

- `"garlic (tsp)"` → `"garlic"`
- `"salt (to taste)"` → `"salt"`
- `"egg (each)"` → `"egg"`
- `"butter (optional)"` → `"butter"`
- `"1 (tsp) garlic powder"` → `"garlic powder"`

The new `_paren_unit_re` pattern matches `(unit)`, `(tsp)`, `(oz)`, `(g)`,
`(optional)`, `(to taste)`, bare numbers like `(2)`, and all other common
unit/modifier annotations in parentheses. Non-unit parentheticals (e.g.
`"chicken (diced)"`) are left in place since `diced` is not in the match list.

---

## Phase 10 — Patch 25: ingredient token cleanup

### Bug fix

**Ingredient pills no longer show "unit" or leading punctuation**

`_ingredient_names_from_text()` in `recipe_discovery.py` had two issues:

- **"unit" not stripped** — Home Chef uses `unit`/`units`/`each` as the
  unit-of-measure for countable items (e.g. `"1.0 unit Eggs"`). These were
  not in the stripping regex so the pill displayed `"unit eggs"`. Fixed by
  adding `units?|each` to the unit alternation.

- **Word boundary missing** — short units like `g` (grams) were greedily
  matching the first letter of ingredient names (e.g. the `g` in `garlic`).
  Added `\b` after the unit group so it only matches standalone units.

- **Leading punctuation not stripped** — after quantity removal some strings
  started with `, `, `. `, `- ` etc. Added a secondary regex
  `^[,\.;:\-\s]+` to strip those before lowercasing.

Existing stubs will show corrected ingredient names after the next nightly
re-scrape (or manual ⚡ Scrape on the Sources page).

---

## Phase 10 — Patch 24: ingredient pill click fix

### Bug fix

**Ingredient pills now open and close correctly on a single click**

`onMouseDown` was firing on initial press, making it feel like the popup
only appeared while holding. Switched back to `onClick` on all pill buttons.
The expanded pill container now has its own `onClick={e=>e.stopPropagation()}`
so clicks on any action button never bubble up to the card's close handler.

---

## Phase 10 — Patch 23: ingredient pill popup — close + recipe removal

### Bug fix

After selecting an action from the ingredient pill popup, the popup was
staying visible. Fixed by calling `setActiveIngPop(null)` before awaiting
the API call in every action handler.

**Exclude / Dislike now removes the recipe from the suggestion list
immediately** via a new `onRemove` prop passed from `PlannerPage`:
`onRemove={id=>setSug(s=>s.filter(x=>x.recipe_id!==id))}`. No page
refresh required — the card disappears as soon as the preference is saved.

---

## Phase 10 — Patch 22: ingredient pill inline horizontal options

### Changed

Replaced the dropdown popup (hidden by card overflow) with an inline
horizontal expansion. Clicking a pill now replaces it in-place with a
yellow bar showing three colour-coded action buttons on the same line:
📌 Staple (green) · 🚫 Exclude (red) · 👎 Dislike (amber) · ✕ dismiss.

---

## Phase 10 — Patch 21: ingredient pill — add to staples / exclude / dislike

### New feature

Clicking any red "Need to buy" ingredient pill now opens an inline option
bar. Three actions available:

- **📌 Pantry staple** — `POST /pantry/staples`; ingredient assumed always
  on hand, excluded from future shopping lists
- **🚫 Never suggest** — appends to `prefs.excluded_items`; hard filter,
  recipe never shown again
- **👎 Soft dislike** — appends to `prefs.disliked_items`; −15 pts
  scoring penalty, recipe deprioritised but not hidden

---

## Phase 10 — Patch 20: ruamel YAML separator fix

### Bug fix

`recipe_sources.yaml` was triggering two ruamel warnings on every load:
- "expected a single document in the stream" — ruamel was writing a `---`
  document separator on round-trip saves then failing to re-read it
- "string index out of range" — a separate ruamel round-trip bug

Fixed in `config_files.py`:
- `_yaml.explicit_start = False` — prevents ruamel from ever writing `---`
- `_yaml.load_all(f)` takes only the first document, silently tolerating
  existing files that already have the stray separator

---

## Phase 10 — Patch 19: Database page — recipe cache stats + wipe

### New features

**🗄️ Database page** added to the Tools nav section.

**📊 Recipe Cache stats panel** — loads on page open; shows three summary
tiles (Total stubs / Mealie-linked / Rejections) and a breakdown table of
stub counts grouped by source domain with percentage share. Refreshes
automatically after a wipe.

**🧹 Wipe Recipe Cache** — two-step confirmation (click → red confirm box
→ confirm) calls `DELETE /config/recipe-cache`. Deletes all unconfirmed
stubs (`mealie_slug IS NULL`) and all rejection records; leaves preferences,
pantry, meal plan history, and Mealie-linked recipes untouched. Reports
counts in a green success banner and auto-refreshes the stats.

New endpoints: `GET /config/recipe-stats`, `DELETE /config/recipe-cache`.

---

## Phase 10 — Patch 18: print recipe shows full instructions

### Bug fix

The print view was showing ingredients only — instructions were always
empty for unconfirmed recipe stubs because:
1. `_scrape_recipe()` never called `scraper.instructions_list()`
2. No `scraped_instructions_json` column existed on the `Recipe` model
3. `get_print_data` only fetched instructions from Mealie (unavailable
   for unconfirmed stubs)

Fixed:
- `recipe_discovery.py` — `_scrape_recipe()` now calls
  `scraper.instructions_list()` with a newline-split fallback;
  `_update_row_from_detail()` stores steps to `scraped_instructions_json`
- `models.py` — new `scraped_instructions_json TEXT` column
- `database.py` — migration for the new column (auto-applied on startup)
- `routers/recipes.py` — `get_print_data` loads `scraped_instructions_json`
  as the baseline; Mealie instructions still override when available

Existing stubs will gain instructions after the next nightly re-scrape.

---

## Phase 10 — Patch 17: UNIQUE constraint hardening + Home Chef source

### Bug fixes

**UNIQUE constraint crash hardened in two places**

`matching_engine.py` — Pool A (Mealie favourites) now does a pre-check
query by `mealie_slug` before creating a local stub row. If a concurrent
suggest request already created the stub, the existing row is reused
instead of hitting the UNIQUE constraint on `recipes.source_url`. If the
flush still fails for any reason, the exception is caught, the pending
row is expunged, and the race winner is fetched instead.

`recipe_discovery.py` — the `db.flush()` for newly scraped recipes is
now wrapped in try/except with the same recovery pattern. Also added
URL trailing-slash normalization in `_extract_recipe_urls` so
`/meals/foo-bar` and `/meals/foo-bar/` are treated as the same canonical
URL and never both enter the scrape pipeline.

### New features

**Home Chef added as a second discovery source**

`recipe_discovery.py` — new `_is_homechef_host()` and `_HOMECHEF_MEAL_RE`
recognise Home Chef's `/meals/{slug}` URL pattern. Category index pages
(`/recipes/chicken`, `/recipes/beef` etc.) are correctly rejected; only
individual meal pages pass through to the scrape budget.

`recipe_sources.yaml` — 32 dinner-focused category page URLs added across
13 categories: Chicken (3 pages), Beef (3), Pork (3), Steak (2),
Poultry (2), Seafood (2), Fish (2), Shrimp, Salmon, Cod, Pasta (2),
Vegetarian (2), Customer Favorites (2), Staff Picks (2),
Calorie-Conscious (2), Carb-Conscious, Lamb. Breakfast, Dessert,
Smoothie, Salad, and Protein Packs are deliberately excluded.
Estimated ~300–400 unique dinner recipe candidates after dedup.

## Phase 10 — Patch 16: push shopping list to Bring!

### Added

**"📤 Push to Bring!" on the shopping list**

Pushes every BUY item (not pantry_check, not using_from_pantry — those
are already on hand) to a [Bring!](https://www.getbring.com) shopping
list, so the list shows up natively on your phone in a purpose-built,
multi-user, checkable grocery app instead of only in this UI. Considered
and ruled out: a direct push to Apple Reminders (no public API exists —
would require either a self-hosted CalDAV server or an on-device
Shortcut, both heavier than this) and AnyList (its only Python client is
Rust-based bindings around a reverse-engineered API — more fragile to
containerize than Bring!'s pure-Python library).

- New `backend/app/bring_client.py` — thin wrapper around the `bring-api`
  PyPI package (unofficial; also what Home Assistant's own Bring!
  integration is built on). Verified against the actual installed
  package (0.5.7) rather than assumed from its README, since the
  published release lags the docs — notably its top-level `bring_api`
  package doesn't re-export `Bring`/exceptions like the README implies;
  they're imported from submodules instead.
- `BRING_EMAIL` / `BRING_PASSWORD` env vars (`.env`, `docker-compose.yml`)
  — same pattern as `MEALIE_BASE_URL` / `MEALIE_API_TOKEN`. Leave blank to
  disable; the push button then returns a clear "not configured" error
  instead of failing silently.
- New `Preference.bring_list_name` column + migration — only needed if
  your Bring! account has more than one list; a single-list account is
  auto-selected with no configuration. Settings → Preferences has a
  "Show my Bring! lists" button (`GET /meal-plan/bring/lists`) to check
  exact spelling.
- `POST /meal-plan/shopping-list/push-to-bring` — builds the shopping
  list the same way `GET /shopping-list` does, then pushes it. Safe to
  run twice for the same week: Bring!'s add-item call updates an
  existing item's quantity in place rather than duplicating it.
- Item "specification" (the subtitle Bring! shows under each item name)
  prefers the rounded package quantity ("2 x 12 oz can") over the raw
  combined quantity ("2.33 tbsp") when a package size is configured —
  literally what to pick up off the shelf.

## Phase 10 — Patch 15: combine same ingredients in the shopping list

### Added

**New `backend/app/ingredient_utils.py`**

- `normalize_name()` — reduces a raw ingredient name or free-text line to
  a canonical grouping key: strips a leading quantity/unit, leading
  prep-instruction words ("finely chopped", "large"), a parenthetical
  aside ("(15 oz can)"), a trailing comma-appended prep note (", diced"),
  and lightly singularises. So `"Yellow Onion"`, `"yellow onions"`, and
  `"2 large yellow onions, diced"` all collapse to `"yellow onion"`
  instead of producing three separate shopping-list lines.
- `to_base()` / `from_base()` / `unit_family()` — light unit conversion
  within the volume family (tsp/tbsp/fl oz/cup/pt/qt/gal) and the mass
  family (g/kg/oz/lb) only. `"2 tbsp olive oil"` in one recipe and
  `"1 tsp olive oil"` in another now combine into one `"2.33 tbsp olive
  oil"` line instead of two. Units outside those families, or a
  name/unit combination that doesn't share a family with another entry,
  are never force-combined.
- `parse_scraped_ingredient()` — best-effort quantity/unit/name split for
  a raw scraped ingredient line, e.g. `"2 1/2 cups chopped yellow
  onion"`. Recipes not yet imported into Mealie previously stored each
  ingredient as an opaque string with no parsed quantity, so they could
  never combine with anything, in the shopping list or with each other.
  They now feed into the same aggregation as Mealie-sourced ingredients.
- `names_match()` — shared "is this the same shopping-list item?" check
  used for staple detection, tracked-pantry matching, and package-size
  rounding.

### Fixed

**Ingredients weren't combining across recipes**

- `_aggregate()` in `shopping_list.py` now groups by normalized name
  first, then merges quantities within a compatible unit family, instead
  of keying strictly on the raw (name, unit) pair straight from Mealie —
  which meant `"Onion"` and `"onions"`, or `2 tbsp` + `1 tsp` of the same
  ingredient, always produced separate buy-list lines.

**Found while testing this patch — staple/pantry matching could silently
drop a real ingredient from the buy list**

- The old staple check (`"garlic powder" in "garlic" or "garlic" in
  "garlic powder"`) is a plain substring test, so a raw ingredient like
  `"garlic"` was incorrectly classified as covered by the `"garlic
  powder"` staple entry and silently removed from the buy list, even
  though they're different products. `names_match()` now recognises the
  adjective-before-noun cases the substring check was originally meant to
  catch (`"kosher salt"` ~ `"salt"`, `"extra virgin olive oil"` ~ `"olive
  oil"`) while excluding cases where a name is followed by a
  product-changing suffix (`powder`, `extract`, `paste`, `sauce`,
  `broth`, `stock`, `seasoning`, `flakes`, `juice`, `spray`). Applied to
  staple detection, tracked-pantry matching, and package-size rounding.

## Phase 10 — Patch 14: fix blank Preferences tab

### Fixed

**Settings → Preferences/Equipment tabs rendered completely blank for any household with no `Preference` row**

- There was never a UI flow that called `POST /preferences` for a newly
  created household — only the deployment-interview docstring implied one
  existed. Any household without a row (including one recreated after a
  clean DB reset) got a silent 404 from `GET /preferences/{household_id}`,
  and the frontend swallowed it with `.catch(()=>{})`, leaving `prefs` as
  `null` forever.
- `{tab==='prefs'&&prefs&&<div>...}` (and the same pattern on the Equipment
  tab) had no fallback branch for a null `prefs` — so the tab area rendered
  nothing at all: no spinner, no error, no call to action.

### Changed

- `POST /households` now also creates a default `Preference` row for the
  new household in the same request.
- `GET /preferences/{household_id}` now auto-creates a default row on first
  read instead of 404ing, so any household that predates this patch (e.g.
  yours, recreated after the Patch 11 DB reset) self-heals the moment the
  Settings page loads — no manual DB fix needed.
- `SettingsPage` now shows a spinner while preferences are loading and a
  "Couldn't load preferences: ... [Retry]" message on genuine failure,
  instead of rendering nothing, as defense-in-depth if `GET /preferences`
  ever fails for an unrelated reason (e.g. backend down).

## Phase 10 — Patch 13: fix rating/review filter for HelloFresh; filter at score time

### Fixed

**`min_scraped_rating` / `min_scraped_reviews` were silently inert for every HelloFresh recipe**

- `recipe-scrapers`'s `HelloFresh` class implements `.ratings()` but not
  `.ratings_count()` — calling it raised `AttributeError`. The old quality
  gate wrapped both calls in one `try/except`, so that exception caused the
  entire check to be skipped and every recipe silently passed regardless of
  rating.  Since HelloFresh is now the only source, this setting has had
  zero effect since Patch 11.
- New `_extract_rating_and_reviews()`: rating and review count are now
  extracted independently, each wrapped in its own `try/except`. When
  `ratings_count()` fails, it falls back to reading the review count
  straight from the page's schema.org JSON-LD
  (`aggregateRating.ratingCount`), which HelloFresh does populate correctly
  — confirmed live (4.19★ / 1834 reviews on a sample recipe).

### Changed

**Rating/review filtering moved from scrape time to score time**

- `_scrape_recipe()` no longer rejects recipes based on rating — it always
  returns the scraped detail (with `_rating`/`_reviews` populated when
  available) so a low-rated recipe still gets written to the DB cache
  instead of being silently discarded.
- New `recipes.scraped_rating` (Float) and `recipes.scraped_reviews`
  (Integer) columns (migration included), populated at scrape time.
- `score_cached()` now applies `min_scraped_rating` / `min_scraped_reviews`
  against each stub's stored values before scoring it. This means:
  - Changing the threshold in Discovery Settings takes effect on the
    **entire cached catalog on the very next suggest run** — no waiting for
    a re-scrape cycle.
  - A stub with no rating data yet (not backfilled) passes through rather
    than being dropped, so nothing vanishes while the background job catches
    up.
  - No more wasted scrape budget repeatedly re-fetching a URL that will
    always fail the same threshold — it's scraped once, cached with its
    rating, and simply excluded from results until the threshold changes or
    the rating does.
- Stubs scraped before this patch (no `scraped_rating` yet) are now treated
  as "stale" by `collect_and_scrape()`'s refresh loop so they get their
  rating backfilled within one scrape budget instead of waiting a full
  `stub_rescrape_days` cycle.

### Where to change the threshold

Recipe Sources page → **Discovery Settings** card → "Min rating (0=off)" /
"Min reviews (0=off)". Both were already present in the UI (Patch 10-era) but
had no real effect for HelloFresh until this patch. Default remains 4.0★ /
50 reviews; set either to `0` to disable that check.

## Phase 10 — Patch 12: nightly background scraping + fast cached scoring

### Added

**Nightly background scrape job (`scrape_job.py`)**

- Daemon scheduler thread runs `collect_and_scrape()` once per day at
  `background_scrape_hour` (default 03:00 server time). Config in
  `recipe_sources.yaml` (`background_scrape_enabled`, `background_scrape_hour`,
  `background_max_scraped`) is re-read every 5 minutes — edits apply without
  restart. Shared lock guarantees no concurrent scrapes.
- Last-run stats persisted to `scrape_status.json` next to the DB; exposed
  via `GET /api/config/scrape-status`. Manual trigger via
  `POST /api/config/scrape-now` and a "Scrape Now" button + status line on
  the Recipe Sources page.

**Scrape-time ingredient tokenization**

- New `recipes.scraped_tokens_json` column (migration included): canonical
  ingredient tokens computed once at scrape time. Scoring now does
  set-intersection against the pantry instead of re-parsing ingredient text
  every run — 10K stubs score in well under a second, so DB growth doesn't
  degrade suggest latency.

**SQLite tuning (`tune_sqlite()`)**

- `PRAGMA journal_mode=WAL` so the nightly writer never blocks daytime
  suggest reads; indexes on `recipes.last_scraped_at` and
  `recipe_rejections.recipe_id`.

### Changed

**Discovery split: scraping and scoring are now decoupled**

- `discover_and_score()` is now cache-first: warm cache (any stub scraped
  within `stub_rescrape_days`) → `score_cached()` only, pure CPU, no network
  I/O, returns in ~1s. Cold cache → synchronous `collect_and_scrape()` with
  the usual progress bar, then scoring.
- `collect_and_scrape()`: crawl directory pages → refresh stale/token-less
  stubs (½ budget) → scrape new URLs (½ budget) → commit. No household
  context, no scoring.
- Timing instrumentation: `score_cached: scored N of M stubs in X ms` logged
  every suggest run so growth-vs-latency stays visible.

## Phase 10 — Patch 11: HelloFresh source; RSS discovery removed

### Changed

**Discovery now uses exactly two sources: HelloFresh + local Mealie**

- `recipe_sources.yaml` rewritten: all RSS blog sources removed; single
  `HelloFresh` source crawled via its 25 server-rendered A–Z recipe
  directory pages (`/pages/sitemap/recipes-a` … `-z`, no X). HelloFresh has
  no RSS feed and bot-gated XML sitemaps, but the HTML directory pages are
  plain link lists that fetch cleanly. The Mealie "proven favourite" pool
  is unchanged (`mealie_min_rating` / `mealie_favorites_count`).

**All RSS functionality removed**

- `recipe_discovery.py`: removed feedparser import, `_clean_feed_url`,
  `_is_valid_feed_url`, `_fetch_feed_urls_with_entries`, `_fetch_feed_urls`,
  `_is_dinner_entry`, and the entire RSS collection phase. The HTML phase is
  now Phase 1 with per-page progress reporting (5–45%).
- `routers/config.py`: removed `feed_urls` from source payloads,
  `feed_pages` from discovery settings, and the `/config/sources/discover`
  RSS autodiscovery endpoint.
- Frontend: "RSS Sources" page renamed "Recipe Sources"; feed-URL form
  field, Discover Feeds modal, and "RSS pages/feed" setting removed; copy
  updated throughout.
- `requirements.txt`: `feedparser` dropped.
- `feed_pages` config key no longer read; stale keys in deployed YAML are
  ignored harmlessly.

### Added

**HelloFresh URL validation (Gap A)**

- `_HELLOFRESH_RECIPE` pattern: HelloFresh recipe URLs must end in the
  24-char hex recipe ID (`/recipes/<slug>-651320e7…`). Hub/category pages
  (`/recipes/american-recipes`, `/eat/top-recipes`) linked from directory
  pages no longer leak into the scrape budget.

**Slug-based non-dinner filter on the HTML path (Gap B)**

- `_is_dinner_url()`: candidate URL slugs (recipe name is in the slug,
  trailing hex ID stripped, hyphens → spaces) are screened against
  `non_dinner_title_keywords` before scraping — the HTML-path equivalent of
  the old RSS title filter. Applies to every HTML source.

## Phase 10 — Patch 6: real-time progress bar via polling endpoint

### Changed

**Progress bar now shows actual backend progress (not fake stages)**

Previous: a `setInterval` cycling through 6 hardcoded labels every 900ms,
completely disconnected from real work. Stuck at "Almost done..." for minutes
on large RSS runs.

New architecture:
- `recipe_discovery.py` — thread-safe in-memory progress store
  (`_progress: dict[household_id → {pct, message}]`) with `set_progress()`,
  `get_progress()`, and `clear_progress()` helpers. `discover_and_score()`
  writes to the store at every meaningful milestone:
  - 3%  — "Building recipe catalog..."
  - 5–45% — "Fetching {source name} ({n} of {total} feeds)..." per RSS feed
  - 47%  — "Scoring {n} known recipes against your pantry..."
  - 48–54% — "Re-scraping stale recipe: {title}..." (only stale stubs)
  - 57%  — "Discovering new recipes (0 of {budget})..."
  - 57–90% — "Scraping recipe {n} of {total}: {slug}..." per new URL
  - 93%  — "Ranking and filtering suggestions..."
  - 100% — "Done!"
- `routers/meal_plan.py` — new `GET /meal-plan/suggest/progress?household_id=`
  endpoint returns the current `{pct, message}` for a household
- `index.html` — `loadSuggestions` polls the progress endpoint every 800ms
  while the main `GET /suggest` call is in-flight, updating the bar with real
  messages. Polling clears immediately when the response arrives.


## Phase 10 — Patch 5: progress bar fix + UNIQUE constraint crash

### Bug fixes

**Progress bar caused blank screen (index.html)**
The `progress` and `progressLabel` state variables were referenced in the JSX
render but never declared — the `useState` declarations and the updated
`loadSuggestions` body were both missing from the previous patch due to a
failed string replacement. React threw a ReferenceError at render time,
unmounting the entire page tree. Fixed by adding both `useState` declarations
and replacing `loadSuggestions` with the staged progress version.

**UNIQUE constraint crash on recipe suggestions (recipe_discovery.py)**
`IntegrityError: UNIQUE constraint failed: recipes.source_url` was thrown
during `GET /meal-plan/suggest` when a previously-rejected recipe's URL
re-appeared in an RSS feed. Root cause: `existing_stubs` was filtered by
`excluded_recipe_ids` (correct for Pool X scoring), but `all_known` was derived
from that same filtered list — so rejected stubs' URLs were silently absent
from the dedup set. Pool Y then treated the URL as new and tried to INSERT a
duplicate row.

Fix (two parts):
- Decoupled `all_stub_urls` (used for Pool Y dedup, includes ALL stubs) from
  `existing_stubs` (used for Pool X scoring, excludes rejected recipes)
- Added a safety-net DB lookup in `_process` before any blind INSERT: if the
  URL already exists despite passing the dedup check, the existing row is
  updated instead of crashing


## Phase 10 — Patch 4: six-fix update

### Bug fixes

**#6 — RSS Sources 500 "string index out of range"**
Added a PyYAML fallback in `config_files.load_yaml()`. If ruamel.yaml raises
any non-YAML exception during load (common ruamel round-trip bugs trigger bare
`IndexError` etc.), the loader retries with `yaml.safe_load` and logs a warning.
The config always loads now; ruamel is still used for writes so comments survive.
Added `pyyaml` to `requirements.txt`.

### New features

**#1 — Progress bar when generating suggestions**
The Generate Suggestions button now shows a staged animated progress bar while
the backend scores recipes. Six stage labels cycle at 900ms intervals
("Checking pantry...", "Scoring recipe matches...", etc.) and the bar jumps to
100% + "Done!" when the response arrives, then fades out. No backend changes.

**#2 — RSS feed discovery**
New "🔍 Discover Feeds" button on the RSS Sources page opens a modal where you
enter any food blog URL. The backend (`POST /config/sources/discover`) fetches
the page, parses `<link rel="alternate">` autodiscovery tags, and probes 10
common feed path patterns (`/feed/`, `/rss/`, `/atom.xml`, etc.). Found feeds
appear as a list; clicking "Use" pre-fills the Add Source form.

**#3 — Non-dinner keywords from rejection**
Rejecting a recipe as "Not applicable" or "Side dish" now opens a modal showing
clickable chips extracted from the recipe title (single words and bigrams, minus
stop-words). Selected chips are appended to the `non_dinner_title_keywords` list
in `recipe_sources.yaml` via `PUT /config/non-dinner-keywords`, so similar
recipes are pre-filtered from future RSS imports automatically.

**#4 — Mealie favourites at bottom of suggestions**
The final sort in `matching_engine.py` now uses `(is_favorite_int, -score)` as
the key, so newly discovered Pool B recipes float above Mealie favourites within
the same score range. Pool A (Mealie) still appears — just below new discoveries.

**#5 — Print / Save PDF shopping list**
A "🖨️ Print / Save PDF" button now appears at the bottom of the Shopping List
step. Clicking it opens a new tab with a clean print-ready HTML layout (all UI
chrome hidden via `@media print`) and immediately triggers the browser's print
dialog. Works for printing or saving as PDF via "Save as PDF" in the dialog.


## Phase 10 — Patch 3: sources 500 fix, load-more button

### Bug fixes

**RSS Sources page — Internal Server Error on load**
`DiscoverySettingsIn` schema was missing `stub_rescrape_days` (added in Phase 10).
If the user saved Discovery Settings from the UI after Phase 10 was deployed,
that field was silently stripped from the YAML on the PUT. Subsequent reads of
the config could then fail depending on downstream code paths, surfacing as an
opaque 500. Fix:
- Added `stub_rescrape_days: int = 7` to `DiscoverySettingsIn`
- Added `disc["stub_rescrape_days"]` to the PUT handler so the field survives saves
- Added `stub_rescrape_days` to the Discovery Settings form in the UI (editable)
- Wrapped `list_sources()` and `get_discovery_settings()` in try/except so any
  YAML or filesystem error surfaces as a meaningful message instead of a generic 500

### New features

**Load more suggestions when all are rejected**
When every suggestion in the pool has been rejected, a yellow banner now appears
below the last card with two buttons:
- **Load N+5 suggestions** — increments the suggestion count by 5 and re-runs
  the scoring engine, pulling a larger slice from the known recipe pool
- **Refresh same pool** — re-runs the engine with the same count (useful if
  you want a different random sample without increasing the batch size)


## Phase 10 — Patch 2: YAML-driven UI lists

### Bug fixes & improvements

All hardcoded lists in the frontend now read from their YAML sources via API.
Adding, removing, or relabelling items in any YAML file is immediately reflected
in the UI with no code change.

**Rejection reasons (`rejection_reasons.yaml`)** — previously hardcoded 6 options;
now fetched from `GET /recipes/rejection-reasons` on page load. All 14 reasons
(including `not_applicable` and `side_dish` added in Patch 1) appear automatically.
The modal trigger was also fixed: the old code checked for `'missing_equipment'`
(a key that doesn't exist in the YAML). It now uses `_EQUIP_KEYS` and
`_DISLIKE_KEYS` sets keyed to the actual YAML keys
(`cook_method_unavailable`, `cookware_unavailable`, `dislike`, `allergy`,
`disliked_ingredient`).

**Cooking methods (`cooking_vocabulary.yaml`)** — removed two hardcoded
`KNOWN_METHODS`/`METHODS` constants. Both the rejection feedback modal and the
Settings Equipment tab now fetch from `GET /config/cooking-methods`.

**Cookware (`cooking_vocabulary.yaml`)** — was entirely absent from the UI.
Added a Cookware section to the Settings → Equipment tab (renamed from
"Cooking Methods") showing all 16 items from the YAML as checkboxes backed
by `prefs.available_cookware`.

**Skill level (`cooking_vocabulary.yaml`)** — `skill_level` existed on the
Preference model but was never editable. Added a Skill level dropdown to
the Settings → Preferences tab, populated from `vocab.skill_levels`.


## Phase 10 — Scraper cache, rejection fix, dedup import, failed-import links

### New features

**Scraper caching — Pool X no longer re-scrapes fresh stubs**
Added `last_scraped_at` (DATETIME) column to the `recipes` table. Pool X now
checks this timestamp against a configurable TTL (`stub_rescrape_days`, default 7)
in `recipe_sources.yaml`. Stubs scraped within the TTL window are scored entirely
from cached DB columns (`scraped_ingredients_json`, `scraped_time_minutes`,
`scraped_description`) with no HTTP request. Only stale or never-scraped stubs
hit the network. Logged as `Pool X CACHE HIT` at DEBUG level.

**Failed import deep links**
When a recipe fails to import into Mealie, the Import Status card now shows two
links: "↗ Import in Mealie" (opens Mealie's import-by-URL form pre-filled with
the recipe URL) and "🔗 View original" (direct link to the source page for
manual copy/paste). Requires `GET /config/mealie-url` (new endpoint) which
returns `MEALIE_BASE_URL` and `group_slug` so the frontend can build deep links
without hardcoding the Mealie address.

**Duplicate import prevention**
Before importing a recipe, `confirm_selections` now calls
`mealie_client.find_recipe_by_url()` which queries Mealie's `orgURL` field.
If Mealie already holds that recipe (e.g. after a DB reset), the existing slug
is synced back to our DB and the import is skipped. Prevents duplicate recipes
accumulating in Mealie across re-runs.

### Bug fixes

**Rejecting one Mealie recipe rejected all Mealie recipes**
Pool A recipes that had no local DB row were assigned `recipe_id = None`.
All such recipes shared `None` as their ID, so rejecting any one caused all
others to visually flip to the rejected state in the UI (the frontend
`rejected` array contained `null`, which matched every other `null` recipe_id).
Fix: `matching_engine.py` now creates a minimal local `Recipe` stub for any
Mealie recipe that lacks one, assigning a real unique ID before building the
suggestion dict. The title is backfilled once the Mealie detail response
arrives.

### Migration
`database.py` `run_migrations()` automatically adds `recipes.last_scraped_at`
on first startup — no manual action needed.


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
