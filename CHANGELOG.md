# Recipe Planner — Changelog

## Phase 10 — Patch 76: fix Bring! categorisation — send article_id not name

### Bug fix

Items in Bring! were still all "own items" with no grocery section. Root cause:
`_build_catalog_lookup` built `{name: name}` — we sent the translated name
("Chicken Breast") as `itemId`. Bring! treats any unrecognised string as an
"own item" even when it looks like a catalog name.

The correct approach: build `{name_lower: article_id}` and send the
**article_id** (the key from `articles.en-US.json`) as `itemId`. Bring! maps
article IDs to their catalog entries with predefined grocery sections
(Meat & Fish, Produce, Dairy, Bakery, etc.).

`_match_catalog` now returns `Optional[str]` (the article_id, or None on no
match). The send loop uses `article_id` when found and falls back to the
raw ingredient name (own item) when not. Debug logging added to diagnose
matches and misses per item.

VERSION bumped to 10.76.

---

## Phase 10 — Patch 75: confirm step servings redesign

### Improvement

**Confirm step recipe cards completely redesigned for servings clarity:**

Each recipe now shows:
- **Base servings** — the recipe's original yield (e.g. "Base: 4")  
- **Target servings stepper** — large +/− buttons with the serving count
  prominently displayed; defaults to household size, adjustable per recipe
- **Scale badge** — green ×1.5 (scaling up) or amber ×0.8 (scaling down);
  "×1 (as written)" when no scaling needed
- Cook time, carbs, and shopping count in the subtitle row

The scale factor is calculated live as `round(target / base, 1)` and shown
inline so it's clear what the shopping list will receive. After locking in,
the confirmed servings count shows next to each recipe in the import results
panel.

The header note explains: "Adjust servings per recipe — the shopping list
will scale ingredients accordingly."

VERSION bumped to 10.75.

---

## Phase 10 — Patch 74: fix blank page — numPeople state undeclared

### Bug fix

Patch 73 added `numPeople` state to `PlannerPage` but the `useState(4)`
declaration and the household load (`GET /households/{hid}`) were missing
from the committed file — only the `useEffect` that used `numPeople` was
written, causing an immediate `ReferenceError` and a blank page.

Added:
- `const [numPeople, setNumPeople] = useState(4)` in PlannerPage
- `get('/households/${hid}').then(h => setNumPeople(h.num_people))`
  in the mount useEffect

VERSION bumped to 10.74.

---

## Phase 10 — Patch 73: auto-scale recipe servings to household size

### Improvement

The confirm step serving inputs now default to the household's `num_people`
setting rather than the recipe's base serving count. When a recipe is added
to the confirmed list, a `useEffect` pre-populates `servingOverrides` for
all confirmed recipes with the household size — so the shopping list scales
correctly immediately without any manual adjustment.

The household's `num_people` is loaded at PlannerPage mount via
`GET /households/{hid}` and stored as `numPeople` state (defaults to 4
while loading). Users can still override per-recipe from the confirm step.

The base serving count ("base: 4 servings") is still shown as a reference
alongside the adjustable input.

VERSION bumped to 10.73.

---

## Phase 10 — Patch 72: fix Bring! catalog loading — items now categorised

### Bug fix

Bring! items were still showing as uncategorised "own items" despite the
catalog matching code added in Patch 62. Root cause: 

`reload_article_translations()` builds its list of locales to load from
`user_list_settings`, which was never populated (we hadn't called
`reload_user_list_settings()` first). With empty settings the list fell
back to just `[user_locale]`. The library then **skips** `de-CH` (the
Bring! default locale) because it expects a local file — so if the account
locale was `de-CH`, zero catalog files were downloaded and
`_Bring__translations` stayed empty.

Fix: before loading translations, call
`set_list_article_language(listUuid, "en-US")` to explicitly set the
list's item language, then `reload_user_list_settings()` to reflect it,
then `reload_article_translations()`. This forces the English catalog to
be downloaded. Added logging for catalog size and a warning if it's empty.

VERSION bumped to 10.72.

---

## Phase 10 — Patch 71: fix confirm step blank — servingOverrides in wrong component

### Bug fix

`servingOverrides` state was declared inside `RecipeSearchPage` but used
inside `PlannerPage` — causing a `ReferenceError` that crashed the confirm
step with a blank page and no console output.

Fixed: `servingOverrides` / `setServingOverrides` moved to `PlannerPage`
where it belongs (lines 608 and 868 use it). `RecipeSearchPage` gets its
own separate `searchServings` / `setSearchServings` local state for the
serving adjustment on the search add flow.

VERSION bumped to 10.71.

---

## Phase 10 — Patch 70: fix _make_yaml scope; fix confirm page blank

### Bug fixes

**`_make_yaml` not defined — protein category loading failed**
`load_yaml()` was updated to call `_make_yaml()` for a fresh ruamel
instance, but `_make_yaml` was never defined in the file. Added the
function definition before `load_yaml` so it's in scope at call time.

**Confirm step blank — wrong prefs field name**
The serving input default used `prefs?.default_num_people` which doesn't
exist on the Preferences object (the field is `num_people` on Household,
not Preferences). Changed to plain `4` as the safe fallback when no
scraped servings are available — the user can still adjust the input.

VERSION bumped to 10.70.

---

## Phase 10 — Patch 69: per-recipe servings adjustment on confirm step

### New feature

**Servings input on the Confirm step**

Each recipe on the Confirm step (Step 5) now shows a "Servings:" number
input. The default is the recipe's scraped serving count (or household
size if unknown). Adjust before clicking "Lock In" to scale that recipe's
shopping quantities independently — useful for making a double batch of one
dish or a smaller portion of another.

The override is stored as `servings_override INTEGER` on `WeeklySelection`
(auto-migrated) and applied in the shopping list generator: if an override
is set, it replaces `household.num_people` as the target when calculating
the ingredient scale factor. Works for both Pool A (Mealie) and Pool B
(scraped) recipes.

The serving adjustment in Recipe Search (Patch 67) also stores its value
via the same `servings_overrides` field in the `WeeklySelectionCreate`
payload.

VERSION bumped to 10.69.

---

## Phase 10 — Patch 68: fix confirm_selections — mealie_client.is_configured missing

### Bug fix

`confirm_selections` called `mealie_client.is_configured()` which didn't
exist — the module only had `_check_configured()` which raises rather than
returns. Added `is_configured() -> bool` as a public function that returns
`True` when both `MEALIE_BASE_URL` and `MEALIE_API_TOKEN` are set.
`_check_configured()` now delegates to it.

VERSION bumped to 10.68.

---

## Phase 10 — Patch 67: fix blank search page; servings display; YAML fix

### Bug fixes

**Recipe search page was blank**
`inPlan` state (`useState(new Set())`) and its `useEffect` loader were
missing from `RecipeSearchPage` — the JSX used `inPlan.size` which crashed
the component before anything rendered. Added the missing state, effect,
and button logic from Patch 66.

**ruamel.yaml state leakage causing warnings on unrelated files**
`load_yaml()` reused the module-level `_yaml` singleton. When one file
caused a ruamel exception that left the parser in a bad internal state,
the next file load inherited that state and also failed. Fixed by calling
`_make_yaml()` inside `load_yaml()` to create a fresh parser per call —
warnings on `rejection_reasons.yaml` and `cooking_vocabulary.yaml` should
now stop.

### New features

**🍽 Servings shown on recipe cards and search results**
`scraped_servings` now appears next to ⏱ cook time and 🌾 carbs on
suggestion cards and in Recipe Search results. Added to `SuggestedRecipe`
schema and all Pool A/B outputs.

**Serving count adjustment in Recipe Search add flow**
When a recipe has servings data, a small number input appears next to
"+ Add to plan" in Recipe Search. The default is the scraped serving count;
change it to adjust how many servings are added to the shopping list for
that recipe. The value is passed as `servings_override` in the
`history/add` payload.

VERSION bumped to 10.67.

---

## Phase 10 — Patch 66: recipe search marks current week plan status

### Improvement

The Recipe Search page now loads the current week's confirmed selections on
mount (`GET /meal-plan/selections`). Recipes already in this week's plan
show a **"✓ In this week's plan"** green badge immediately — no need to add
them again. The subtitle shows how many recipes are already planned.

After clicking **+ Add to plan**, the recipe is added to the `WeeklySelection`
table (via `history/add`) and the button updates to the green badge
immediately — the status persists when searching for other recipes in the
same session.

The button is now styled as `btn-primary` (blue) for not-yet-added recipes
to make it more prominent.

VERSION bumped to 10.66.

---

## Phase 10 — Patch 65: global recipe search page

### New feature

**🔍 Recipe Search page** (Planning → Recipe Search in nav)

Searches the full recipe database by title — all scraped stubs from
HelloFresh, Home Chef, and Mealie — not just the current week's suggestions.

- Type and press Enter or click Search
- Results ranked: exact word matches first, then alphabetical
- Each result shows cook time, carbs, protein category badge, Mealie badge,
  star rating, blocked status, and source link
- **+ This week** — add directly to the current week's plan
- **🚫** — block the recipe (same as feedback step)

Backend: `GET /api/recipes/search?q=...&household_id=...&limit=50`
searches `Recipe.title` with SQLAlchemy `contains`, over-fetches and
re-ranks by exact word boundary match, returns rejection/rating status
for the household.

VERSION bumped to 10.65.

---

## Phase 10 — Patch 64: suggestion search; unblock from Past Meals

### New features

**🔍 Search on Suggestions page**

A search input appears in the suggestions step header. Typing filters
recipes by title in real-time — protein category groups update to reflect
only matching recipes. Clearing the search (✕ button) restores the full
list. The filter is client-side so it works instantly without a re-fetch.

**✅ Unblock recipes from Past Meals page**

The 📆 Past Meals page now shows a "🚫 Blocked Recipes" card at the bottom
listing all permanently blocked recipes. Each row has:
- **✅ Unblock** — removes the permanent rejection record so the recipe can
  appear in suggestions again
- **+ This week** — optionally add it directly to the current week's plan

The history endpoint (`GET /meal-plan/history`) now returns
`{weeks: [...], blocked: [...]}` instead of a plain array. The frontend
handles both formats for backwards compatibility.

New backend endpoint: `DELETE /api/recipes/{recipe_id}/reject?household_id=`
removes all permanent rejections for a recipe+household pair.

VERSION bumped to 10.64.

---

## Phase 10 — Patch 63: fix carb scraping; fix week picker timezone; TZ env var

### Bug fixes

**Carb data never populated — nutrition not extracted from recipe-scrapers**

`_scrape_recipe()` called `scraper.title()`, `scraper.ingredients()`, etc.
but never called `scraper.nutrients()`. The returned dict had no `nutrition`
key, so `_update_row_from_detail()` always got an empty dict and
`scraped_carbs` was never set. Fixed: `scraper.nutrients()` is now called
and the result included in the returned dict as `nutrition`. Existing stubs
need a re-scrape (nightly job or manual ⚡ Scrape) to populate carb data.

**Week picker starts on wrong day — UTC vs local timezone**

All date calculations used `d.toISOString().split('T')[0]` which always
returns the UTC date. Users in UTC-5 to UTC-12 saw Tuesday instead of
Monday because the UTC date was ahead of their local date. Fixed by
introducing `localISO(d)` — a helper that formats dates using local time
(`getFullYear/getMonth/getDate`) rather than UTC. Applied to:
- `weekMonday()` — default week initialisation
- Week picker offset buttons
- Pantry expiry `today` comparison
- Pantry expiry "soon" (7-day) threshold

**TZ environment variable added to Unraid template**

New "Timezone" field (default `America/Los_Angeles`, Display: always) so
the container's system timezone matches the host. This also fixes the
nightly scrape schedule (3 AM runs in the correct local timezone) and any
Python `datetime` comparisons in the backend that depend on local time.

VERSION bumped to 10.63.

---

## Phase 10 — Patch 62: Bring! catalog matching for ingredient categorisation

### Bug fix

Shopping list items pushed to Bring! were all appearing as uncategorised
"own items" (no Produce / Dairy / Meat section). Bring! only auto-categorises
items when the name exactly matches an entry in their article catalog — our
parsed ingredient names ("boneless chicken breast", "garlic cloves") didn't
match exactly.

**Fix:** after login, `reload_article_translations()` is called to load
Bring!'s article catalog for the user's locale. `_build_catalog_lookup()`
inverts this into a `{lower_name: canonical_name}` reverse index.
`_match_catalog()` then tries three strategies in order:

1. Exact match (case-insensitive)
2. Catalog entry contained in our name — e.g. "boneless chicken breast" →
   "Chicken Breast" (picks the longest/most specific match)
3. Our name contained in a catalog entry — e.g. "garlic" → "Garlic"

If no match is found the original name is used (becomes an "own item" as
before). Matched names are logged at DEBUG level for diagnostics.

The catalog contains ~1,000+ entries per locale and loads in ~200 ms;
it runs once per push operation.

VERSION bumped to 10.62.

---

## Phase 10 — Patch 61: background Mealie import + recipe categories

### New features

**Background Mealie import**

`POST /meal-plan/selections` (confirm) now saves `WeeklySelection` and
`MealPlanEntry` rows synchronously then schedules the Mealie import as a
FastAPI `BackgroundTask`. The response returns immediately with status
`"queued"` for recipes pending import — the user can proceed to the
shopping list without waiting. The shopping list uses scraped ingredients
for Pool B recipes so it works before the import completes.

Background import uses a separate `SessionLocal` DB session so the
request session can close cleanly.

**Mealie recipe categories**

When a recipe is imported, two categories are set:
- **Dinner** — always applied
- **Protein category** (Chicken, Pork, Beef, Pasta, Fish, Shellfish,
  Vegetarian) — derived from `_classify_protein()`, skips "Other"

`mealie_client.set_recipe_categories(slug, names)` and
`_get_or_create_category(name)` added — mirrors the tag find-or-create
pattern. Both are best-effort (log + continue on failure).

The `⏳ Importing in background…` status shows in the import results
panel on the Confirm step. `history/add` endpoint updated to also pass
`BackgroundTasks` through to `confirm_selections`.

VERSION bumped to 10.61.

---

## Phase 10 — Patch 60: remove artificial suggestion count cap

### Bug fix

`GET /meal-plan/suggest` had `le=50` on the `num` query parameter, causing
a 422 validation error for any request with `num > 50`. The Load More button
increments by 5 each click, so the 55th request would always fail.

Fixes:
- Backend `num` query param raised from `le=50` to `le=500`
- Frontend numSug input `max={30}` removed (no upper limit on suggestions step)
- Default suggestions preference input raised from `max={30}` to `max={200}`

VERSION bumped to 10.60.

---

## Phase 10 — Patch 59: carbs NA display; protein categories always sync; settings page move

### Bug fixes + improvements

**Carbs always shown on recipe cards**
Previously `🌾 Xg carbs` only appeared when data was present. Now always
shows `🌾 NA` when `carbs_per_serving` is null so the card layout is
consistent and the missing data is visible rather than silently absent.

**Protein categories always sync from image (code-owned)**
`protein_categories.yaml` was user-owned (only copied if missing), so
containers running before Patch 57 kept the old 7-category file and the
Sources page showed stale categories. Moved to code-owned: the entrypoint
now always syncs it from the image defaults on every container start,
same as `recipe_sources.yaml`. New categories (Pasta, Fish, Shellfish)
will appear immediately on next container restart without needing a rebuild.

**Protein category ordering moved to Preferences page**
The "🥩 Protein Category Order" UI has moved from the Sources page to the
Preferences page (Settings → Protein Groups tab). Ordering is still global
(writes to `protein_categories.yaml`), just displayed in a more logical
location alongside other household-level preferences.

VERSION bumped to 10.59.

---

## Phase 10 — Patch 58: pasta/fish/shellfish categories; scrape block; auto-skip feedback; concurrent discovery

### New features

**Pasta + Fish/Shellfish split protein categories**

Seven categories become nine:
- 🍝 Pasta (order 5) — pasta, spaghetti, fettuccine, gnocchi, noodle, ramen, udon, pad thai, lo mein, etc.
- 🐟 Fish (order 6) — salmon, tuna, cod, tilapia, halibut, trout, mahi, sea bass, haddock, swordfish, snapper, etc.
- 🦐 Shellfish (order 7) — shrimp, prawn, crab, lobster, scallop, clam, mussel, squid, calamari, oyster, etc.

Final order: Chicken → Pork → Turkey → Beef → Pasta → Fish → Shellfish → Vegetarian → Other.

**Block planning while scraping**

`GET /meal-plan/suggest` now returns `503 Service Unavailable` if a scrape
is currently running. The Generate Suggestions button checks scrape status
before firing and shows a warning banner if a scrape is active, with a
"Check" button to re-poll. Button is disabled until scraping completes.

**Auto-skip Last Week Feedback if nothing to review**

If `pending-feedback` returns zero items, Step 1 is skipped automatically
and the planner advances directly to Step 2 (Pantry Review). No flash of
the empty-state card. On error, also skips forward gracefully.

**Concurrent category page discovery**

Phase 1 (fetching category/directory pages) now uses `ThreadPoolExecutor`
with one worker per source (capped at 4). Category pages from different
sources hit different domains so there is no rate-limit risk. Expected
speedup: 3–4× for the discovery phase (57 pages across 2 sources).
Recipe scraping (Phase 2) remains sequential to respect per-site limits.

New helper `recipe_discovery.is_scraping()` uses a non-blocking lock
acquire to check if a scrape is in progress without blocking the caller.

VERSION bumped to 10.58.

---

## Phase 10 — Patch 57: vegetarian default classification + carbs on recipe cards

### Bug fix

**Unclassified recipes default to Vegetarian, not Other**

The protein classifier now distinguishes between:
- **No animal protein detected** → `vegetarian` (pasta, salads, soups, grain bowls)
- **Unlisted animal protein detected** → `other` (lamb, duck, venison, bison, etc.)

`_OTHER_PROTEIN_KEYWORDS` catches proteins not in the standard categories.
If none of these appear in the title or tokens either, the recipe defaults
to vegetarian — which is correct for plant-based dishes.

The hardcoded `_PROTEIN_DEFAULTS` in `config_files.py` updated to include
unlisted protein keywords on the "Other" category.

Vegetarian keyword matching is unchanged; explicit vegetarian keywords still
trigger the vegetarian category first.

### New feature

**🌾 Carbs per serving on recipe cards**

Displayed next to score, cook time, and shopping count on every recipe
suggestion card. Populated from the `carbohydrateContent` field in the
recipe's JSON-LD nutrition block (scraped at discovery time).

New DB column `scraped_carbs REAL` (auto-migrated). Existing stubs will
show carbs after the next nightly re-scrape. Added to `SuggestedRecipe`
schema and both Pool A and Pool B suggestion outputs.

VERSION bumped to 10.57.

---

## Phase 10 — Patch 56: fix Past Meals rating and block not persisting

### Bug fixes

**Rating called wrong endpoint — 404 on every star click**

The rate buttons on the Past Meals page and Last Week Feedback step were
calling `POST /meal-plan/entries/{id}/rate` but the correct endpoint is
`POST /meal-plan/entries/{id}/review`. The 404 response was caught by the
error handler so the toast never fired, and `historyDone` was never set —
clicks appeared to do nothing.

Fixed in both places: feedback step (Step 1) and Past Meals page.

**`entry_id` null for some recipes — rating silently skipped**

The history endpoint looked up `MealPlanEntry` by exact `(household_id,
recipe_id, week_start_date)` match. For older data where the entry's
week might not match the selection's week, the lookup returned `None`
→ `entry_id: null` → the frontend guard `if(!r.entry_id)` returned early.

Added a fallback: if the exact-week query returns nothing, look up any
`MealPlanEntry` for the same recipe and household (most recent first).

VERSION bumped to 10.56.

---

## Phase 10 — Patch 55: fix protein grouping — field stripped by Pydantic schema

### Bug fix

All suggestions were showing under "Other" despite correct classification.

Root cause: `GET /meal-plan/suggest` uses `response_model=schemas.WeeklySuggestion`
→ `SuggestedRecipe`. `protein_category` was not in the `SuggestedRecipe`
schema, so Pydantic silently stripped it from every response. The frontend
received `undefined` for every recipe and fell back to `'other'`.

Fix: added `protein_category: Optional[str] = "other"` to `SuggestedRecipe`.
Classification now flows through correctly: Chicken, Pork, Beef, Seafood,
Vegetarian, Other.

VERSION bumped to 10.55.

---

## Phase 10 — Patch 54: fix protein classification — hardcoded fallback defaults

### Bug fix

All recipes were being grouped under "Other" because `get_protein_categories()`
returned an empty list when `protein_categories.yaml` wasn't present in the
running container (containers built before Patch 22 lack the file).

Fixed by adding `_PROTEIN_DEFAULTS` — a hardcoded list of the 7 standard
categories — as a fallback when the YAML file is missing or empty.
Classification now works immediately on any running container without
requiring a rebuild. After a rebuild the YAML file takes precedence and
user edits from the Sources page are honoured.

---

## Phase 10 — Patch 53: fix default suggestions count + feedback dedup

### Bug fixes

**Default number of suggestions not applied**

`numSug` was hardcoded to `useState(10)` — the household preference
`default_num_suggestions` was never used for the initial value because it
loads asynchronously. Fixed: the prefs fetch callback now calls
`setNumSug(p.default_num_suggestions)` immediately after `setPrefs(p)`,
so the configured default is applied as soon as prefs resolve.

**Rated/blocked meals still appearing in planner feedback**

`pending-feedback` filtered `MealPlanEntry.rating.is_(None)` on individual
entries. If a recipe was selected in multiple weeks, rating one week's entry
left other entries (same recipe, different week) still appearing as
unreviewed. Also, ratings set on the Past Meals page weren't suppressing
the recipe in the planner feedback step.

Fixed: the query now pre-computes `rated_ids` — the set of all recipe_ids
that have ANY rated MealPlanEntry for this household — and excludes them
entirely via `recipe_id NOT IN (...)`. Combined with `blocked_ids`, the
`processed_ids` set ensures a recipe disappears from the feedback step as
soon as it's rated or blocked anywhere in the app.

---

## Phase 10 — Patch 52: continuous feedback across all past meals

### Changes

**`GET /meal-plan/pending-feedback` — all unreviewed meals, not just last week**

The feedback step now returns every past meal that hasn't been rated or
permanently blocked, sorted most-recent-week first and deduped by recipe
(same dish served across multiple weeks appears once). The current planning
week is excluded.

**Past Meals page — rate and block actions**

Each recipe on the 📆 Past Meals page now shows:
- ⭐ Star rating (1–5) — pushes to Mealie, marks as favourite if ≥ threshold
- 🚫 Block — permanently rejects from future suggestions  
- Already-rated recipes show their star badge; already-blocked show 🚫 Blocked

History endpoint enhanced with `entry_id`, `rating`, and `is_blocked` fields.

**Feedback step — shows week date per card**

Each card in the Step 1 feedback list now shows the week the meal was
served (📅 Mon dd mmm yyyy) so it's clear which meals are being reviewed.
Description updated from "last week" to "all meals not yet rated".

---

## Phase 10 — Patch 51: fix blocked recipes not removed from feedback list

### Bug fix

After clicking 🚫 Block or 🔄 Make again this week in the Last Week
Feedback step, the recipe card remained visible (only showing a badge).
Fixed: both actions now remove the card from the feedback list after a
400 ms delay (enough time for the toast to appear and the user to see the
confirmation before it disappears).

Rating (⭐) keeps the card visible so the user can adjust stars.

---

## Phase 10 — Patch 50: protein category grouping in suggestions

### New feature

**Suggestions grouped by protein type**

Recipes in the suggestion list are now grouped under category headers
(🍗 Chicken, 🐷 Pork, 🦃 Turkey, 🥩 Beef, 🐟 Seafood, 🥦 Vegetarian,
🍽️ Other) based on keyword matching against the recipe title and
ingredient tokens.

Classification priority:
1. Title keyword match (strongest signal, first-match wins by category order)
2. Ingredient token match (most hits wins)
3. Falls back to "Other"

**Configurable order via Sources page**

The "🥩 Protein Category Order" section on the Sources page lets you:
- Reorder categories with ↑↓ buttons or by editing the order number
- Toggle categories on/off (hidden categories merge into "Other")
- Save changes to `protein_categories.yaml`

Default order: Chicken (1) → Pork (2) → Turkey (3) → Beef (3) →
Seafood (4) → Vegetarian (5) → Other (6).

**New files / endpoints:**
- `backend/app/data/protein_categories.yaml` — default config (code-owned)
- `GET /config/protein-categories` / `PUT /config/protein-categories`
- `config_files.get_protein_categories()` / `save_protein_categories()`
- `recipe_discovery._classify_protein(title, tokens)` used in both Pool A
  (Mealie favourites) and Pool B (discovered) suggestion outputs

`protein_categories.yaml` is user-owned — copied to `/data/` on first
container start, never overwritten by the entrypoint.

**VERSION bumped to 10.50**

---

## Phase 10 — Patch 49: fix Block action in Last Week Feedback

### Bug fix

The 🚫 Block button on the Last Week Feedback step was sending
`{reason: 'disliked', permanent: true}` but the reject endpoint expects
`{reason_category: 'dislike'}` (the key from `rejection_reasons.yaml`).
Fixed the payload to use `reason_category: 'dislike'` which is the
permanent rejection key.

---

## Phase 10 — Patch 48: fix print pantry list blank output

### Bug fix

Print Pantry List on the shopping list page was generating a blank table.
Root cause: the button relied on the `pantry` React state loaded at mount,
which could be empty or stale by the time the user reaches the shopping list
step.

Fixed: the onClick is now `async` and fetches fresh pantry data directly
from `GET /pantry?household_id=...` at print time. Falls back to cached
state on error. Shows a warning toast if there are no items to print, and
adds an item count line under the heading.

---

## Phase 10 — Patch 47: week picker + last week feedback step

### New features

**Step 0 — Select Week**
The planner now opens with a week picker showing the current week plus the
next 3 Mondays as buttons. Selecting a week sets it as the planning target
for the entire session. The week display in the page header updates live.

**Step 1 — Last Week Feedback**
After selecting a week, the planner fetches meals from the PREVIOUS week
(`GET /meal-plan/pending-feedback`) and shows them for review before
planning begins. For each meal:
- ⭐ **Rate (1–5 stars)** — calls `POST /meal-plan/entries/{id}/rate`,
  pushed to Mealie; 4★+ marks the recipe as a favourite
- 🚫 **Block** — calls `POST /recipes/{id}/reject` with `permanent: true`;
  recipe is suppressed from future suggestions
- 🔄 **Make again this week** — calls `POST /meal-plan/history/add` to
  add the recipe directly to the current week's confirmed plan

All existing steps shift up by 2:
- Pantry Review → Step 2
- Week Intent → Step 3
- Suggestions → Step 4
- Confirm → Step 5
- Shopping List → Step 6

New backend endpoint: `GET /meal-plan/pending-feedback?household_id=&week_start_date=`

---

## Phase 10 — Patch 45: Ollama integration for ingredient normalisation

### New feature

**Optional local LLM ingredient normalisation via Ollama**

When `OLLAMA_BASE_URL` is set, the shopping list generator calls the local
Ollama instance after ingredient aggregation (Step 3b) to canonicalise
ingredient names before pantry matching and package rounding:

- `"boneless skinless chicken breast"` → `"chicken breast"`
- `"yellow onion"` → `"onion"`
- `"parmigiano-reggiano"` → `"parmesan"`
- Meaningful distinctions preserved: `"cherry tomato"` ≠ `"roma tomato"`

The call is a single batch request per shopping list (not per ingredient),
returning a JSON mapping. If Ollama is unavailable or returns malformed
output, the code falls back to the existing deterministic behaviour
silently.

**New module** `backend/app/ollama_client.py`:
- `is_configured()` — checks if `OLLAMA_BASE_URL` is set
- `is_available()` — health check against `/api/tags`
- `generate(prompt, model, expect_json)` — raw completion
- `normalize_ingredient_names(names)` — ingredient normalisation

**New environment variables** (all optional):
- `OLLAMA_BASE_URL` — e.g. `http://192.168.111.189:11434`
- `OLLAMA_MODEL` — default `qwen3:latest`
- `OLLAMA_TIMEOUT` — default `30` seconds

**Unraid template** — three new advanced fields added for the above.
Ollama status (URL, model, available) now included in `GET /config/version`
response so the UI can surface it.

Recommended model: `qwen3:latest` (already installed). `gemma3:4b` is a
fast fallback. No new model installation required.

---

## Phase 10 — Patch 44: fix unit words appearing in ingredient names

### Bug fix

Unit words like "teaspoon", "tablespoon", "ounce" were leaking into the
ingredient name on the shopping list. Root cause: the previous regex
approach in `_extract_ingredients_from_raw` used two separate operations
(strip qty+unit, then extract unit separately) that could get out of sync,
especially for formats like `"1 Teaspoon(s) Salt"` where the `(s)` suffix
broke the unit-stripping regex.

Replaced with a single `_ING_FULL_RE` regex that captures `qty`, `unit`,
and `name` in one match, handling:
- `"2 unit(s) Garlic Cloves"` → garlic cloves (unit/each → no unit)
- `"1 Teaspoon(s) Salt"` → 1 tsp salt
- `"3 tablespoons of butter"` → 3 tbsp butter ("of" stripped)
- `"1 (15 oz) can black beans"` → 1 can black beans (paren qty stripped first)
- `"2 cloves garlic, minced"` → 2 cloves garlic (comma suffix stripped)
- `"salt and pepper to taste"` → no qty, name preserved as-is

All 12 test cases pass.

---

## Phase 10 — Patch 43: use scraped ingredients for shopping list (bypass Mealie re-scrape)

### Architecture change

The shopping list generator now uses the ingredient strings stored in
`Recipe.scraped_ingredients_json` (captured by recipe-scrapers at
discovery time) as the primary source for ingredient data, instead of
re-fetching from Mealie.

**Why:** Mealie's URL importer does its own scraping pass which often
stores `quantity: null` on ingredients it can't parse structurally.
recipe-scrapers captures the full raw note string ("2 cups flour",
"1/2 tsp salt") which is far more reliably parseable.

**New function** `_extract_ingredients_from_raw()` parses raw ingredient
strings: strips leading quantity (including fractions like 1/2), extracts
unit keyword, normalizes the ingredient name using the same pipeline as
the scoring engine.

**Logic:**
- Pool B / discovered recipes — have `scraped_ingredients_json` →
  use raw strings directly, no Mealie API call needed
- Pool A / Mealie-native recipes — no local scrape → fall back to
  Mealie as before

Also adds `_parse_servings_str()` to parse our stored `scraped_servings`
value (e.g. "4 servings") for the scale calculation.

---

## Phase 10 — Patch 42: fix shopping list "as needed" quantities

### Bug fix

Shopping list items showing "as needed" instead of quantities.

Root cause: when Mealie imports a recipe from URL and the scraper cannot
parse the structured ingredient format, it stores `quantity: null` on the
ingredient row. The shopping list extractor treated null as 0, which
`_round_to_package` converted to `None`, producing "as needed" in the UI.

Fix: `_extract_ingredients` now falls back to parsing the `note` field
(the raw ingredient string Mealie always stores) when `quantity` is null.
The fallback regex extracts a leading number (including fractions like
½, 1/2) and a unit keyword (cups, tbsp, tsp, oz, g, cloves, etc.) from
strings like `"2 cups flour"`, `"1/2 tsp salt"`, `"3 cloves garlic"`.
Ingredients with genuinely no quantity (e.g. `"salt and pepper"`) still
correctly show "as needed".

---

## Phase 10 — Patch 41: fix missing close button on print windows

### Bug fix

The Patch 39 regex substitution used to add "← Close print window" links
used a pattern that didn't match the escaped `<\/script>` sequence inside
JavaScript template literals. As a result, only the pantry print window
had the close button; the single recipe, print-all, and shopping list
print windows did not.

All four print windows now have a "← Close print window" link inserted
directly before the `window.print()` call. The link is hidden by
`@media print` so it doesn't appear on the printed page itself.

---

## Phase 10 — Patch 40: fix extraBuyItems + client error logging

### Bug fixes

**`setExtraBuyItems` not defined** — Shopping list "Add to buy list" feature
was broken because the `extraBuyItems` state declaration was lost during a
sync. Re-added `const [extraBuyItems, setExtraBuyItems] = useState([])` to
`PlannerPage`.

### New feature

**All UI errors forwarded to the Logs page**

A new `logError(message, context)` helper POSTs every API error to
`POST /api/logs/client`, which writes it into the backend ring buffer at
the appropriate log level. This means any failed API call (import, scrape,
shopping list generation, etc.) now appears on the **Logs** page with label
`client` alongside backend errors, making it much easier to diagnose issues
without opening the browser console.

`POST /api/logs/client` added to `main.py` — accepts `{level, message,
context}` and logs via the Python `client` logger.

---

## Phase 10 — Patch 39: print UX, print pantry move, past meals page, version link

### Bug fixes

**Print windows now have a "← Close print window" link**
All four print functions (single recipe card, print-all recipes, print
shopping list, print pantry) now include a "← Close print window" link
inside the printed page. After the print dialog closes the link is visible
and navigates back. The main app tab is always preserved since all print
windows open with `window.open('','_blank')`.

**Print Pantry moved to Shopping List page (Step 4)**
The 🖨️ Print Pantry List button has been moved from the Confirm step
(Step 3) to the Shopping List step (Step 4), alongside the existing print
shopping list button. This is a more natural placement — you'd want to
print your pantry reference while reviewing the shopping list.

### New feature

**📆 Past Meals page**
A new "Past Meals" page (under Planning in the nav) shows every recipe
selected in previous weeks, grouped by week date. Each recipe shows title,
cook time, Mealie status, and a source link. Clicking **+ Add to this
week** imports the recipe directly into the current week's plan (same
import flow as confirm-selections) without needing to go through the
suggestion engine.

Backend: `GET /meal-plan/history` returns past selections grouped by week,
deduped. `POST /meal-plan/history/add` reuses the confirm-selections flow
to import and tag the recipe.

### Improvement

**Version indicator — brighter color + GitHub hyperlink**
The version string in the sidebar footer and mobile topbar now renders in
`#60a5fa` / `#93c5fd` (blue, readable against the dark nav) and links to
`https://github.com/rgabrielson11/recipe-planner` in a new tab.

---

## Phase 10 — Patch 38: remove Marley Spoon source

### Change

Marley Spoon removed as a discovery source.

Investigation showed that individual recipe pages are JavaScript-rendered
and return an empty shell to the scraper — no ingredients or instructions
are available without either a headless browser or a `remember_spree_user_token`
subscriber cookie. A public Contentful API exists (used in Marley Spoon's
hiring coding challenges) but contains only a small historical catalog
unrelated to the current weekly menu.

Without ingredients, Marley Spoon recipes cannot be used for pantry
scoring, shopping list generation, or recipe printing — the core
functions of the app.

Removed:
- `_MARLEYSPOON_MEAL_RE` regex and `_is_marleyspoon_host()` function
- Marley Spoon branch in `_looks_like_recipe_url()`
- `_ms_week_urls()` dynamic week expansion in `collect_and_scrape()`
- Marley Spoon entry in `recipe_sources.yaml`

HelloFresh and Home Chef remain as the two discovery sources.

---

## Phase 10 — Patch 37: fix circular JSON error on Generate Suggestions

### Bug fix

**"Converting circular structure to JSON" crash on Generate Suggestions and Refresh**

Root cause: in Patch 35 `loadSuggestions` was given a `countOverride`
parameter so the Load More button could pass the new count directly. Two
buttons still used the bare `onClick={loadSuggestions}` pattern (no arrow
wrapper), which causes React to pass the SyntheticEvent as the first
argument. Since `countOverride ?? numSug` evaluates the event as truthy,
`n` became the SyntheticEvent object instead of a number. That object was
then included in the POST body — `num_suggestions: event` — and
`JSON.stringify` hit the circular reference: SyntheticEvent →
nativeEvent.target → HTMLButtonElement → __reactFiber$ → circular.

Fix: wrapped both bare `onClick={loadSuggestions}` calls in arrow
functions (`onClick={()=>loadSuggestions()}`) so no argument is passed and
`countOverride` defaults to `undefined`, falling back to `numSug` as
intended.

Affected buttons: **Generate Suggestions** and **↺ Refresh**.

---

## Phase 10 — Patch 36: version display in nav

### New feature

**Version string shown in the sidebar and mobile topbar**

- `backend/VERSION` file tracks the current version (`10.35`)
- `GET /api/version` endpoint reads `VERSION` from `/app/VERSION`
  (baked into the Docker image) and also returns the git short hash
  when running from a git checkout
- Desktop: version shown at the bottom of the sidebar nav in small text
  (e.g. `v10.35 (a4998aa)`)
- Mobile: version shown in the topbar next to the page label
- `backend/Dockerfile` now copies `VERSION` to `/app/VERSION` at build
  time so the correct version is always available inside the container

To update the version with each future patch, bump `backend/VERSION`.

---

## Phase 10 — Patch 35: fix Load More suggestion count

### Bug fix

The **➕ Load N suggestions** button was always loading 5 fewer recipes
than its label promised. Root cause: `setNumSug(next)` schedules a React
state update asynchronously, but `loadSuggestions()` fired immediately
after in the same closure, still reading the stale `numSug` value.

Fixed by giving `loadSuggestions` an optional `countOverride` parameter.
The Load More button now passes the new count directly:
`loadSuggestions(numSug + 5)` — so the API calls always use the intended
number regardless of when React flushes the state update.

---

## Phase 10 — Patch 34: fix mobile layout — missing state declarations

### Bug fix

The mobile layout patch (Patch 33) was applied from an incomplete patch
file. The deployed commit was missing three pieces:

- `const [navOpen,setNavOpen]=useState(false)` — App state for tracking
  whether the drawer is open
- `const pageLabel=NAV.find(n=>n.key===page)?.label||''` — current page
  label shown in the mobile top bar
- `Nav` component signature updated from `({page,setPage})` to
  `({page,setPage,navOpen,onClose})` with the overlay `<div>` and
  `nav-open` class toggling

Without these the app crashed on load on both desktop and mobile.

---

## Phase 10 — Patch 33: mobile-responsive layout

### New feature

**Full mobile support — hamburger drawer navigation**

On screens ≤ 639 px wide:
- The sidebar hides off-screen (`transform: translateX(-100%)`)
- A dark top bar appears with a ☰ hamburger button, the app name, and
  the current page label
- Tapping ☰ slides the nav in as a drawer overlay; tapping the overlay
  or any nav item closes it
- `.main` expands to full width (`margin-left: 0`, reduced padding)

Responsive grid fixes applied via `@media(max-width:639px)`:
- `.form-row` and `.grid2` collapse to single column
- `.table` uses smaller padding and font size
- `.card` padding reduced; `.step` bar text shrinks
- `.modal` caps at 95 vw

`Nav` component updated to accept `navOpen` and `onClose` props and
render the overlay `<div>` alongside the `<nav>`.
`App` gains `navOpen` state, `pageLabel` for the top bar, and passes
both to `Nav`.

---

## Phase 10 — Patch 32: household delete on Database page

### New feature

**🏠 Households section on the Database page**

Lists all households with name, people count, and ID. Each row has a 🗑️
button that expands inline to a "Delete all data?" / Cancel confirmation
before calling `DELETE /households/{id}`.

Deleting a household removes its pantry items, preferences, weekly
selections, intents, and meal plan history (all cascade via FK). The
shared recipe stub cache is unaffected.

If the deleted household is the currently active one (matches
`localStorage.householdId`), the entry is cleared and the page reloads
so the household gate re-appears for a fresh selection or creation.

The `DELETE /households/{household_id}` endpoint already existed in the
backend — this patch adds the UI only.

---

## Phase 10 — Patch 31: Marley Spoon source + dynamic week URLs

### New features

**Marley Spoon added as a third discovery source**

`recipe_discovery.py` — new `_MARLEYSPOON_MEAL_RE` regex recognises
`/menu/{numeric-id}-{slug}` recipe URLs. `_is_marleyspoon_host()` and a
new branch in `_looks_like_recipe_url` route Marley Spoon URLs through
the meal pattern, rejecting the bare `/menu` hub page and marketing pages.

`recipe_sources.yaml` — single base URL `https://marleyspoon.com/menu`.
The weekly menu page is server-rendered and returns 100+ recipe links per
week with no login required. Individual recipe pages are tried with
`recipe-scrapers` JSON-LD fallback; failures are silent.

**Dynamic week URL expansion — no stale dates**

At scrape time `_ms_week_urls()` computes the most recent Monday and
expands the base URL into 4 weekly URLs (current week + 3 upcoming
Mondays). The YAML never needs manual date updates.

---

## Phase 10 — Patch 30: servings shown on recipe prints

### New feature

Recipe print views (single card and print-all) now show serving size in
the header line alongside cook time and source URL — e.g. `⏱ 35 min · 🍽 4 servings`.

Added `scraped_servings TEXT` column to the `Recipe` model (auto-migrated).
`_update_row_from_detail` stores the value from `scraper.yields()`.
`get_print_data` returns it from the DB and overrides with Mealie's
`recipeServings` / `recipeYield` when the recipe has been imported there.

Existing stubs will show servings after the next nightly re-scrape.

---

## Phase 10 — Patch 29: ingredient token fix + pantry buy list + pantry print

### Bug fixes

**Ingredient tokens cleaned at display time (no re-scrape needed)**

Old stubs scraped before the regex fix still stored tokens like `"unit egg"`
or `"garlic (tsp)"`. A new `_clean_stored_token()` helper is now applied
when loading `scraped_tokens_json` at score time, stripping leading
`unit`/`each` prefixes and parenthetical unit annotations from every token
before it hits the missing-ingredients list. No re-scrape required.

### New features

**"+ Add to buy list" on Pantry Check rows (Step 4 — Shopping List)**

Each item in the green "✓ on hand" Pantry Check section now has a clickable
"+ Add to buy list" action. Clicking it moves the item to a new
**🛒 Added from Pantry** section at the top of the shopping list — useful
when stock is low and you want to buy more. Click again to remove it.
Added-from-pantry items are included in the print output.

**🖨️ Print Pantry List button (Step 3 — Confirm Selections)**

A print button appears above "Lock In & Import to Mealie" that opens a
clean printable pantry snapshot (item, quantity, category, expiry) for the
current week's review.

---

## Phase 10 — Patch 28: print all selected recipes

### New feature

**🖨️ Print All Recipes button on the confirm step**

After confirming selections and importing to Mealie, a **🖨️ Print All
Recipes (N)** button appears above the Mealie Import Status card.

- Fetches full print data for all selected recipes in parallel
- Opens a single print-optimised page with a cover page ("This Week's
  Recipes") followed by each recipe on its own separate page
- Each recipe shows: title, cook time, source URL, description,
  full ingredient list, and numbered instructions
- Fires `window.print()` automatically
- Recipes missing scraped instructions fall back to title + source link
- Page breaks use both `page-break-before` (legacy) and `break-before`
  (modern) so separation works across all browsers

---

## Phase 10 — Patch 27: pantry delete inline confirmation

### Bug fix

**Pantry delete button now works reliably**

The delete button used `window.confirm()` which is silently blocked or
returns `undefined` in many browser/iframe contexts — causing the delete
to never execute. Replaced with an inline two-step confirmation in the
table row: clicking 🗑️ reveals **Yes, delete** and **Cancel** buttons
in-place. No dialog dependency, no blocking.

---

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
