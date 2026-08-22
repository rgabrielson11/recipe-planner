"""
Recipe Discovery Engine — Phase 10 Patch 11
=============================================

Patch 11 removes all RSS / Atom feed functionality.  Recipes come from
exactly two sources:

  • HelloFresh — crawled via their server-rendered A–Z recipe directory
    pages (category_urls in recipe_sources.yaml).  HelloFresh publishes no
    RSS feed and its XML sitemaps are bot-gated, but the HTML directory
    pages are plain link lists and fetch cleanly.  Individual recipe URLs
    always end in a 24-char hex ID (/recipes/<slug>-651320e7…), which the
    URL validator requires so hub/category pages never waste scrape budget.
  • Mealie — the local Mealie library ("proven favourite" pool), selected
    elsewhere via mealie_min_rating / mealie_favorites_count.

Flow (Patch 12: scraping and scoring are decoupled)
----------------------------------------------------
  Nightly (scrape_job.py) or cold-cache fallback — collect_and_scrape():
    1. HTML phase  — fetch category_urls per source, extract recipe links.
                     Exclusion filter strips non-recipe URLs; the non-dinner
                     keyword filter screens URL slugs before scraping.
    2. Stub refresh — re-scrape stale / token-less stubs.  Capped at
                     budget // 2.
    3. New URLs    — scrape new candidates.  Capped at budget // 2.
    4. Quality gate — pages exposing schema.org aggregateRating are rejected
                     if rating < min_scraped_rating OR review_count <
                     min_scraped_reviews.  Pages without ratings pass.
    5. Ingredient tokens are normalized once here and stored on the row.

  On demand (suggest run) — discover_and_score():
    Warm cache → score_cached() only: pure CPU set-intersection against the
    live pantry, no network I/O.  Cold cache → synchronous
    collect_and_scrape() first, with the progress bar.

Stub loop fix (from Phase 7)
------------------------------
  known_urls = only Mealie-imported recipe URLs (mealie_slug IS NOT NULL).
  Stubs are always eligible for re-scraping so they appear in every weekly
  suggestion run, not just the first.
"""

import logging
import re
import time
import random
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse

import threading

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

try:
    from recipe_scrapers import scrape_html
    from recipe_scrapers import SCRAPERS as SUPPORTED_SCRAPERS
    _SCRAPERS_AVAILABLE = True
except ImportError:
    _SCRAPERS_AVAILABLE = False
    SUPPORTED_SCRAPERS  = {}

from app import models, config_files

# ── Per-household progress store ──────────────────────────────────────────────
# Written by discover_and_score() as it advances through stages.
# Read by GET /meal-plan/suggest/progress so the frontend can poll.
_progress_lock: threading.Lock = threading.Lock()
_progress: dict[str, dict] = {}


def set_progress(household_id: str, pct: int, message: str) -> None:
    with _progress_lock:
        _progress[household_id] = {"pct": pct, "message": message}


def get_progress(household_id: str) -> dict:
    with _progress_lock:
        return dict(_progress.get(household_id, {"pct": 0, "message": "Starting..."}))


def clear_progress(household_id: str) -> None:
    with _progress_lock:
        _progress.pop(household_id, None)

log = logging.getLogger(__name__)


# ── URL filtering ─────────────────────────────────────────────────────────────

# Patterns that definitively indicate a non-recipe page.
# Applied to all HTML-scraped URLs.
_EXCLUDE_PATTERNS = re.compile(
    r"""
    /page/\d+                           # pagination  (/page/2, /page/3 …)
    | /category/                        # WordPress category indexes
    | /categories/
    | /tag/                             # WordPress tag indexes
    | /tags/
    | /author/                          # author archive
    | /feed/?(\?|$)                     # feed URL itself
    | /sitemap                          # sitemaps
    | /authentication/                  # AllRecipes auth pages
    | /account/
    | /register
    | /login
    | /logout
    | /search/
    | /wp-                              # WordPress system paths
    | /cdn-cgi/                         # Cloudflare system paths
    | /holiday-recipes/                 # Skinnytaste collection pages
    | /main-ingredient/                 # Skinnytaste collection pages
    | /meal-type/
    | /cuisine/
    | /courses/
    | /method/
    | /diet/
    | /season/
    | /\d{4}/\d{2}/?$                  # bare year/month archive pages
    """,
    re.IGNORECASE | re.VERBOSE,
)

# AllRecipes: individual recipes use /recipe/ (singular); category pages
# use /recipes/ (plural) followed by a number.  Filter out the plural form.
_ALLRECIPES_CATEGORY = re.compile(r"/recipes/\d+/", re.IGNORECASE)

# HelloFresh: individual recipes always end in a 24-char hex ID
# (/recipes/everything-bagel-avocado-toasts-651320e7b6b74f3addadb4f5).
# Hub/category pages (/recipes/american-recipes, /eat/top-recipes) do not,
# so requiring the ID keeps them out of the scrape budget.
_HELLOFRESH_RECIPE = re.compile(r"/recipes/[^/]+-[0-9a-f]{24}$", re.IGNORECASE)


def _is_hellofresh_host(netloc: str) -> bool:
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "hellofresh.com" or host.startswith("hellofresh.")


# Marley Spoon: individual recipes live under /menu/{numeric-id}-{slug}.
# The /menu page itself (and ?week= variants) are server-rendered with all
# recipe links inline — used as category_urls for discovery.
# Individual pages may or may not contain JSON-LD; the scraper will try and
# return None if they don't, so failures are silent and cost-free.
_MARLEYSPOON_MEAL_RE = re.compile(r"^/menu/\d+-[a-z0-9][a-z0-9\-]{3,}$", re.IGNORECASE)


def _is_marleyspoon_host(netloc: str) -> bool:
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "marleyspoon.com" or host.startswith("marleyspoon.")


# Home Chef: individual meals live under /meals/{slug}.
# Slugs can be plain ("coq-au-vin") or have a UUID or keyword suffix
# ("chicken-tacos-363cfbea-...").  Category index pages live under
# /recipes/{category} and must NOT be treated as meal URLs.
_HOMECHEF_MEAL_RE = re.compile(r"^/meals/[a-z0-9][a-z0-9\-]{3,}$", re.IGNORECASE)


def _is_homechef_host(netloc: str) -> bool:
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "homechef.com" or host.startswith("homechef.")


def _looks_like_recipe_url(url: str) -> bool:
    """
    Stricter validator for URLs extracted from HTML category pages.

    HTML category pages often link to sub-categories, tag indexes, and
    pagination pages that look superficially like recipes.  This function
    applies extra heuristics to filter them out before they waste scrape budget.
    """
    parsed = urlparse(url)
    path   = parsed.path.rstrip("/")

    if parsed.query:
        return False

    # Require at least 2 path segments — filters bare /recipes/ category roots
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return False

    if _EXCLUDE_PATTERNS.search(path):
        return False

    # AllRecipes category pattern (/recipes/80/ is a category, /recipe/12345/ is a recipe)
    if "allrecipes.com" in parsed.netloc and _ALLRECIPES_CATEGORY.search(path):
        return False

    # HelloFresh: require the trailing 24-char hex recipe ID — everything
    # else on the domain is a hub/category/marketing page.
    if _is_hellofresh_host(parsed.netloc):
        return bool(_HELLOFRESH_RECIPE.search(path))

    # Home Chef: accept /meals/{slug} paths; reject /recipes/* (category indexes),
    # /our-menu, /signup, /how-it-works, and any other marketing pages.
    if _is_homechef_host(parsed.netloc):
        return bool(_HOMECHEF_MEAL_RE.match(path))

    # Marley Spoon: accept /menu/{id}-{slug} paths only.
    # The bare /menu and /menu?week= pages are category_urls (fetched directly);
    # only numbered recipe paths should enter the scrape budget.
    if _is_marleyspoon_host(parsed.netloc):
        return bool(_MARLEYSPOON_MEAL_RE.match(path))

    host = parsed.netloc.lstrip("www.")
    if host in SUPPORTED_SCRAPERS:
        return True

    # Generic fallback: path explicitly contains "recipe"
    if re.search(r"/(recipe[s]?/|recipe[s]?-)", path, re.IGNORECASE):
        return True

    return False


# ── Dinner pre-filter ─────────────────────────────────────────────────────────

def _build_non_dinner_re(keywords: list[str]) -> Optional[re.Pattern]:
    """
    Compile a word-boundary regex from the non_dinner_title_keywords list.
    Returns None if the list is empty (filter disabled).
    """
    if not keywords:
        return None
    escaped = [re.escape(kw.strip()) for kw in keywords if kw.strip()]
    if not escaped:
        return None
    # \b word boundary; longest-first for correct alternation ordering
    escaped.sort(key=len, reverse=True)
    pattern = r"\b(" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _is_dinner_url(url: str, non_dinner_re: Optional[re.Pattern]) -> bool:
    """
    Returns True if a candidate URL's slug looks like a dinner recipe.

    Recipe slugs contain the recipe name (HelloFresh:
    /recipes/everything-bagel-avocado-toasts-651320e7…).  The slug is
    de-hyphenated and screened against the compiled non-dinner pattern
    BEFORE scraping so we don't waste scrape budget on pancakes and
    cheesecakes.
    """
    if non_dinner_re is None:
        return True   # filter disabled

    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    # Strip a trailing hex ID (HelloFresh) so it can't confuse matching
    slug = re.sub(r"-[0-9a-f]{24}$", "", slug, flags=re.IGNORECASE)
    text = slug.replace("-", " ").replace("_", " ")

    m = non_dinner_re.search(text)
    if m:
        log.debug("NON-DINNER filter: '%s' (matched '%s')", text, m.group(0))
        return False
    return True


def _same_host(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def _extract_recipe_urls(html: str, base_url: str) -> list[str]:
    """Extract candidate recipe URLs from an HTML page."""
    soup  = BeautifulSoup(html, "lxml")
    found = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].split("?")[0].split("#")[0]
        full = urljoin(base_url, href)
        # Normalize: strip trailing slash so the same recipe URL with and
        # without a trailing slash is treated as a single canonical URL.
        parsed_full = urlparse(full)
        full = parsed_full._replace(path=parsed_full.path.rstrip("/") or "/").geturl()
        if _same_host(full, base_url) and _looks_like_recipe_url(full):
            found.add(full)
    return list(found)


# ── Scraping ──────────────────────────────────────────────────────────────────

def _extract_rating_and_reviews(scraper) -> tuple[Optional[float], Optional[int]]:
    """
    Pull star rating and review count off a recipe-scrapers scraper object.

    Not every site's scraper class implements both methods — HelloFresh's
    ratings() works but ratings_count() raises AttributeError even though
    the review count is present in the page's schema.org JSON-LD
    (aggregateRating.ratingCount).  When the library method fails, fall back
    to reading that field directly off the parsed schema data.
    """
    rating: Optional[float] = None
    try:
        raw_rating = scraper.ratings()
        if raw_rating is not None:
            rating = float(raw_rating)
    except Exception:
        pass

    reviews: Optional[int] = None
    try:
        raw_count = scraper.ratings_count()
        if raw_count is not None:
            reviews = int(raw_count)
    except Exception:
        try:
            agg = (scraper.schema.data or {}).get("aggregateRating") or {}
            raw_count = agg.get("ratingCount") or agg.get("reviewCount")
            if raw_count is not None:
                reviews = int(float(raw_count))
        except Exception:
            pass

    return rating, reviews


def _scrape_recipe(
    url: str,
    user_agent: str,
    timeout: int = 20,
) -> Optional[dict]:
    """
    Scrape a single recipe URL using recipe-scrapers.

    Returns a Mealie-compatible detail dict (with "_rating"/"_reviews" keys
    populated where available) on success, or None if the page couldn't be
    scraped at all.  Rating/review-count thresholds are NOT enforced here —
    see score_cached(), which applies them against the stored values so
    threshold changes take effect on the whole cached catalog immediately,
    not just newly-scraped recipes (Patch 13).
    """
    if not _SCRAPERS_AVAILABLE:
        return None
    try:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            log.debug("HTTP %s for %s — skipping", resp.status_code, url)
            return None

        scraper = scrape_html(resp.text, org_url=url)
        rating, reviews = _extract_rating_and_reviews(scraper)

        # ── Extract fields ────────────────────────────────────────────────
        title = ""
        try:
            title = scraper.title() or ""
        except Exception:
            pass

        description = ""
        try:
            description = scraper.description() or ""
        except Exception:
            pass

        ingredients: list[dict] = []
        try:
            for ing_text in (scraper.ingredients() or []):
                ingredients.append({"note": ing_text, "food": None, "quantity": None, "unit": None})
        except Exception:
            pass

        total_time_raw = None
        try:
            mins = scraper.total_time()
            if mins and int(mins) > 0:
                h = int(mins) // 60
                m = int(mins) % 60
                total_time_raw = f"PT{h}H{m}M" if h else f"PT{m}M"
        except Exception:
            pass

        yields_raw = None
        try:
            yields_raw = scraper.yields()
        except Exception:
            pass

        instructions: list[str] = []
        try:
            steps = scraper.instructions_list()
            if steps:
                instructions = [s.strip() for s in steps if s and s.strip()]
        except Exception:
            # Fall back to single instructions() string and split on newlines
            try:
                raw_inst = scraper.instructions() or ""
                if raw_inst:
                    instructions = [s.strip() for s in raw_inst.split("\n") if s.strip()]
            except Exception:
                pass

        tags_list: list[dict] = []
        try:
            keywords = scraper.keywords()
            if keywords:
                for kw in (keywords.split(",") if isinstance(keywords, str) else keywords):
                    tags_list.append({"name": kw.strip()})
        except Exception:
            pass

        if not title or not ingredients:
            log.debug("No title/ingredients at %s — skipping", url)
            return None

        log.debug(
            "Scraped OK '%s' — %d ingredients, time=%s, rating=%s/%s (%s)",
            title, len(ingredients), total_time_raw or "?",
            rating if rating is not None else "?",
            reviews if reviews is not None else "?",
            url,
        )
        return {
            "name":                title,
            "description":         description,
            "tags":                tags_list,
            "recipeCategory":      [],
            "recipeIngredient":    ingredients,
            "recipeInstructions":  [{"text": s} for s in instructions],
            "totalTime":           total_time_raw,
            "recipeServings":      yields_raw,
            "_source_url":         url,
            "_rating":             rating,
            "_reviews":            reviews,
        }

    except Exception as e:
        log.debug("Scrape exception for %s: %s", url, e)
        return None


# ── Scoring ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.lower().strip()


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


def _recipe_text(detail: dict) -> str:
    parts = [detail.get("name", ""), detail.get("description", "")]
    for tag in detail.get("tags", []):
        parts.append(tag.get("name", "") if isinstance(tag, dict) else str(tag))
    for ing in detail.get("recipeIngredient", []):
        if isinstance(ing, dict):
            parts.append(ing.get("note", "") or "")
    return " ".join(p for p in parts if p)


def _ingredient_names_from_text(detail: dict) -> set[str]:
    names: set[str] = set()
    # Strips leading quantity + optional unit from ingredient strings.
    # 'unit'/'units'/'each' added for Home Chef which uses these for
    # countable items (e.g. "1.0 unit Eggs" → "eggs").
    # Word boundary after the unit group prevents short units like 'g' (grams)
    # from greedily matching the first letter of the ingredient name (e.g.
    # the 'g' in 'garlic'). 'units?/each' added for Home Chef countable items.
    _qty_re = re.compile(
        r"^\s*[\d¼½¾⅓⅔⅛⅜⅝⅞\/\.\-]+\s*"
        r"(units?|each|cups?|tbsp|tsp|tablespoons?|teaspoons?|lbs?|oz|g|kg|ml|l|"
        r"cloves?|heads?|bunches?|slices?|pieces?|cans?|packages?|"
        r"pounds?|ounces?|grams?|large|medium|small|whole|fresh|dried|"
        r"pinch(es)?|dash(es)?|handful)?\b\s*",
        re.IGNORECASE,
    )
    # Strip leading punctuation left over after quantity removal (e.g. ", egg", ". salt")
    _leading_punct_re = re.compile(r"^[,\.;:\-\s]+")
    # Strip parenthetical unit annotations anywhere in the string.
    # Matches (tsp), (g), (unit), (each), (oz), (cloves), etc.
    # Also strips bare numbers inside parens like (2) or (1/2).
    _paren_unit_re = re.compile(
        r"\(\s*([\d\/\.]+|units?|each|cups?|tbsp|tsp|tablespoons?|teaspoons?|"
        r"lbs?|oz|g|kg|ml|l|cloves?|heads?|bunches?|slices?|pieces?|cans?|"
        r"packages?|pounds?|ounces?|grams?|large|medium|small|pinch(es)?|"
        r"dash(es)?|handful|optional|to taste)\s*\)",
        re.IGNORECASE,
    )
    for ing in detail.get("recipeIngredient", []):
        raw = (ing.get("note", "") or "").strip() if isinstance(ing, dict) else ""
        if not raw:
            continue
        # Remove quantity + unit prefix
        cleaned = _qty_re.sub("", raw)
        # Remove parenthetical unit annotations (e.g. "garlic (tsp)", "salt (to taste)")
        cleaned = _paren_unit_re.sub("", cleaned)
        # Strip leading punctuation
        cleaned = _leading_punct_re.sub("", cleaned).strip().lower()
        # Take only the first component if comma-separated
        cleaned = cleaned.split(",")[0].strip()
        if cleaned:
            names.add(cleaned)
    return names


_PARSE_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?", re.IGNORECASE)


# Applied to tokens loaded from scraped_tokens_json so stubs scraped before
# the regex fix (which added unit/each stripping and paren cleanup) show
# correct ingredient names without needing a full re-scrape.
_CLEAN_TOKEN_UNIT_RE  = re.compile(
    r"^(units?|each)\s+", re.IGNORECASE
)
_CLEAN_TOKEN_PAREN_RE = re.compile(
    r"\(\s*(units?|each|cups?|tbsp|tsp|tablespoons?|teaspoons?|"
    r"lbs?|oz|g|kg|ml|l|cloves?|pieces?|cans?|pounds?|ounces?|grams?|"
    r"large|medium|small|pinch(es)?|dash(es)?|optional|to taste)\s*\)",
    re.IGNORECASE,
)


def _clean_stored_token(t: str) -> str:
    """Light cleanup applied to pre-stored tokens to fix legacy bad values."""
    t = _CLEAN_TOKEN_UNIT_RE.sub("", t)
    t = _CLEAN_TOKEN_PAREN_RE.sub("", t)
    return t.strip()


def _parse_minutes(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = _PARSE_DURATION_RE.match(raw)
    if not m:
        return None
    total = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return total if total > 0 else None


_METHOD_KEYWORDS: dict[str, list[str]] = {
    "slow_cooker":                 ["slow cooker", "crockpot", "crock pot"],
    "instant_pot_pressure_cooker": ["instant pot", "pressure cooker"],
    "air_fryer":                   ["air fryer", "air-fryer", "airfryer"],
    "sous_vide":                   ["sous vide", "sous-vide"],
    "smoker":                      ["smoked", "smoker", "low and slow"],
    "grill":                       ["grill", "grilled", "barbecue", "bbq"],
    "oven":                        ["bake", "baked", "roast", "roasted", "broil", "braise"],
    "stovetop":                    ["sauté", "saute", "pan-fry", "stir-fry", "sear", "simmer", "skillet"],
    "microwave":                   ["microwave"],
}

_PANTRY_MAX_PTS       = 50
_WEEKLY_HINT_PTS_EACH = 15
_WEEKLY_HINT_MAX_PTS  = 45
_LIKED_PTS_EACH       = 5
_LIKED_MAX_PTS        = 20
_DISLIKE_PTS_EACH     = 15
_DISLIKE_MAX_PENALTY  = 45
_COOK_TIME_PENALTY    = 20
_HARD_REJECT          = float("-inf")


def _score_scraped(
    detail: dict,
    pantry_set: set[str],
    staples_set: set[str],
    prefs: Optional[models.Preference],
    weekly_hints: list[str],
) -> tuple[float, float, list[str]]:
    all_on_hand = pantry_set | staples_set
    # Patch 12: precomputed scrape-time tokens skip per-run text parsing
    _tokens   = detail.get("_ingredient_tokens")
    ing_names = set(_tokens) if _tokens else _ingredient_names_from_text(detail)

    if ing_names:
        on_hand     = {n for n in ing_names if any(_contains(o, n) or _contains(n, o) for o in all_on_hand)}
        missing     = [n for n in ing_names if n not in on_hand]
        overlap_pct = len(on_hand) / len(ing_names)
    else:
        on_hand = set(); missing = []; overlap_pct = 0.0

    score = overlap_pct * _PANTRY_MAX_PTS
    text  = _recipe_text(detail)

    if prefs:
        for excl in (prefs.excluded_items or []):
            if _contains(text, excl):
                return _HARD_REJECT, overlap_pct, missing
        if prefs.available_methods:
            available = set(prefs.available_methods)
            required  = {m for m, kws in _METHOD_KEYWORDS.items() if any(k in text.lower() for k in kws)}
            if required - available:
                return _HARD_REJECT, overlap_pct, missing
        score -= min(sum(_DISLIKE_PTS_EACH for d in (prefs.disliked_items or []) if _contains(text, d)), _DISLIKE_MAX_PENALTY)
        score += min(sum(_LIKED_PTS_EACH for li in (prefs.liked_items or []) if _contains(text, li)), _LIKED_MAX_PTS)
        if prefs.max_cook_time_minutes:
            mins = _parse_minutes(detail.get("totalTime"))
            if mins and mins > prefs.max_cook_time_minutes:
                score -= _COOK_TIME_PENALTY

    score += min(sum(_WEEKLY_HINT_PTS_EACH for h in weekly_hints if _contains(text, h)), _WEEKLY_HINT_MAX_PTS)
    return score, overlap_pct, missing


# ── Public entry point ────────────────────────────────────────────────────────

# Shared between the nightly background job (scrape_job.py) and the cold-cache
# fallback in discover_and_score() so two scrapes never run concurrently
# (SQLite single-writer; shared progress store).
_scrape_lock = threading.Lock()


def _update_row_from_detail(row: models.Recipe, detail: dict) -> None:
    """
    Write the scraped payload to a Recipe row, including precomputed
    ingredient tokens (Patch 12) so scoring never re-parses ingredient text.
    """
    import json as _json
    ing_strings = [
        i.get("note", "") for i in detail.get("recipeIngredient", [])
        if isinstance(i, dict) and i.get("note")
    ]
    instruction_steps = [
        s.get("text", "") for s in (detail.get("recipeInstructions") or [])
        if isinstance(s, dict) and s.get("text", "").strip()
    ]
    row.title                    = detail.get("name") or row.title
    row.scraped_ingredients_json = _json.dumps(ing_strings)
    row.scraped_instructions_json = _json.dumps(instruction_steps)
    row.scraped_servings          = str(detail.get("recipeServings") or "").strip() or None
    row.scraped_time_minutes     = _parse_minutes(detail.get("totalTime"))
    row.scraped_description      = (detail.get("description") or "")[:500]
    row.scraped_tokens_json      = _json.dumps(sorted(_ingredient_names_from_text(detail)))
    row.scraped_rating           = detail.get("_rating")
    row.scraped_reviews          = detail.get("_reviews")
    row.last_scraped_at          = datetime.utcnow()


def collect_and_scrape(
    db: Session,
    budget: Optional[int] = None,
    progress_household: Optional[str] = None,
    wait_for_lock: bool = True,
    source_name: Optional[str] = None,
) -> dict:
    """
    The crawl + scrape half of discovery — no scoring, no household context.

    Fetches source category/directory pages, refreshes stale (or token-less)
    stubs, scrapes new URLs, and stores everything in the DB cache.  Called
    nightly by scrape_job.py and synchronously by discover_and_score() when
    the cache is cold.

    Pass source_name to restrict the HTML crawl phase to a single named
    source (used by the per-source manual scrape button).  Stub refresh and
    new-URL scraping still run normally within the budget.

    Returns a stats dict for the /config/scrape-status endpoint.
    """
    if not _SCRAPERS_AVAILABLE:
        log.warning("recipe-scrapers not installed — scraping disabled")
        return {"error": "recipe-scrapers not installed"}

    acquired = _scrape_lock.acquire(blocking=wait_for_lock)
    if not acquired:
        log.info("collect_and_scrape: another scrape is already running — skipping")
        return {"skipped": "scrape already in progress"}

    t0 = time.perf_counter()
    try:
        cfg           = config_files.get_discovery_config()
        all_sources = config_files.get_enabled_sources()
        # Per-source scrape: restrict HTML crawl to the named source only.
        # Stub refresh and new-URL scraping still run within the budget.
        if source_name:
            sources = [s for s in all_sources if s.get("name") == source_name]
            if not sources:
                log.warning("collect_and_scrape: source '%s' not found or disabled", source_name)
                return {"error": f"Source '{source_name}' not found or disabled"}
            log.info("Per-source scrape: restricting HTML crawl to '%s'", source_name)
        else:
            sources = all_sources
        delay         = float(cfg.get("request_delay_seconds", 2.0))
        user_agent    = str(cfg.get("user_agent", "RecipePlanner/1.0"))
        max_scrape    = int(budget if budget is not None else cfg.get("max_scraped_per_run", 40))
        non_dinner_re = _build_non_dinner_re(list(cfg.get("non_dinner_title_keywords", [])))
        rescrape_days = int(cfg.get("stub_rescrape_days", 7))

        if progress_household:
            def _prog(pct: int, msg: str) -> None:
                set_progress(progress_household, pct, msg)
        else:
            def _prog(pct: int, msg: str) -> None:
                pass

        log.info("=== Scrape start | budget=%d | %d source(s) ===", max_scrape, len(sources))

        # Marley Spoon weekly menu expansion — replace the bare /menu URL with
        # the current week and next 3 weeks so we always scrape fresh menus
        # without needing date updates in the YAML.
        def _ms_week_urls(base: str, weeks: int = 4) -> list[str]:
            """Return base URL + ?week= for the current Monday and next N-1 Mondays."""
            today = datetime.utcnow().date()
            # Find the most recent Monday (weekday 0)
            days_since_monday = today.weekday()
            this_monday = today - timedelta(days=days_since_monday)
            urls = []
            for i in range(weeks):
                week = this_monday + timedelta(weeks=i)
                if i == 0:
                    urls.append(base)          # current week has no ?week= param
                else:
                    urls.append(f"{base}?week={week.isoformat()}")
            return urls

        expanded_sources = []
        for src in sources:
            if _is_marleyspoon_host(urlparse(src.get("category_urls", [""])[0] if src.get("category_urls") else "").netloc):
                new_cat_urls = []
                for u in src.get("category_urls", []):
                    parsed_u = urlparse(u)
                    if _is_marleyspoon_host(parsed_u.netloc) and not parsed_u.query:
                        new_cat_urls.extend(_ms_week_urls(u))
                        log.info("Marley Spoon: expanded to %d week URLs", len(new_cat_urls))
                    else:
                        new_cat_urls.append(u)
                src = dict(src)
                src["category_urls"] = new_cat_urls
            expanded_sources.append(src)
        sources = expanded_sources
        _prog(3, "Building recipe catalog...")

        # URLs already in Mealie — skip entirely
        mealie_imported_urls: set[str] = {
            r.source_url
            for r in db.query(models.Recipe).filter(
                models.Recipe.mealie_slug.isnot(None),
                models.Recipe.source_url.isnot(None),
            ).all()
        }

        # All stubs in DB — used for URL-level dedup regardless of rejection
        # status.  A rejected recipe's URL must stay in all_known so the new-URL
        # pool never re-inserts it (UNIQUE constraint on source_url).
        all_stubs = db.query(models.Recipe).filter(
            models.Recipe.mealie_slug.is_(None),
            models.Recipe.source_url.isnot(None),
        ).all()
        all_known = mealie_imported_urls | {r.source_url for r in all_stubs}

        log.info(
            "Mealie-imported URLs: %d | stubs in cache: %d",
            len(mealie_imported_urls), len(all_stubs),
        )

        # ── Phase 1: collect URLs from HTML category / directory pages ────────
        html_candidate_urls: list[str] = []
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        pages_fetched = 0
        _total_pages  = sum(len(s.get("category_urls", [])) for s in sources)
        _page_idx     = 0
        for source in sources:
            for cat_url in source.get("category_urls", []):
                _page_idx += 1
                _prog(
                    5 + int((_page_idx / max(_total_pages, 1)) * 40),
                    f"Fetching {source['name']} ({_page_idx} of {_total_pages} pages)...",
                )
                try:
                    resp = requests.get(cat_url, headers=headers, timeout=15)
                    if resp.status_code != 200:
                        log.warning("Category page %s returned HTTP %s", cat_url, resp.status_code)
                        continue
                    pages_fetched += 1
                    found = _extract_recipe_urls(resp.text, cat_url)
                    # Non-dinner pre-filter against URL slugs (recipe name is in the slug)
                    dinner     = [u for u in found if _is_dinner_url(u, non_dinner_re)]
                    nd_skipped = len(found) - len(dinner)
                    new = [u for u in dinner if u not in all_known and u not in html_candidate_urls]
                    html_candidate_urls.extend(new)
                    log.info(
                        "HTML '%s' — %s: %d links extracted, %d non-dinner skipped, %d new",
                        source["name"], cat_url, len(found), nd_skipped, len(new),
                    )
                    time.sleep(delay)
                except Exception as e:
                    log.warning("Category page failed (%s: %s): %s", source["name"], cat_url, e)

        # Deduplicate while preserving order
        seen: set[str] = set()
        candidate_urls: list[str] = []
        for u in html_candidate_urls:
            if u not in seen:
                seen.add(u)
                candidate_urls.append(u)
        log.info("Candidate URLs: %d after dedup", len(candidate_urls))
        random.shuffle(candidate_urls)

        # ── Scrape budget split ───────────────────────────────────────────────
        stub_budget = max_scrape // 2
        new_budget  = max_scrape - stub_budget

        # ── Refresh stale / token-less / rating-less stubs ───────────────────
        # Stubs scraped before Patch 13 have no scraped_rating yet — treating
        # them as stale here backfills rating data within one scrape budget
        # instead of waiting a full stub_rescrape_days cycle.
        cutoff = datetime.utcnow() - timedelta(days=rescrape_days)
        stale = [
            s for s in all_stubs
            if not s.last_scraped_at or s.last_scraped_at < cutoff
            or not s.scraped_tokens_json or s.scraped_rating is None
        ]
        random.shuffle(stale)
        stubs_refreshed = 0
        for i, stub in enumerate(stale[:stub_budget]):
            _prog(47 + min(i, 8), f"Re-scraping stale recipe: {stub.title[:50]}...")
            detail = _scrape_recipe(stub.source_url, user_agent)
            time.sleep(delay)
            if detail:
                _update_row_from_detail(stub, detail)
                db.add(stub)
                stubs_refreshed += 1
        log.info("Stub refresh: %d stale/token-less, %d refreshed (budget %d)",
                 len(stale), stubs_refreshed, stub_budget)

        # ── Scrape new URLs ───────────────────────────────────────────────────
        new_recipes = 0
        attempts    = 0
        _new_total  = min(len(candidate_urls), new_budget)
        for url in candidate_urls:
            if attempts >= new_budget:
                break
            _display = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")[:55]
            _prog(
                min(57 + int((attempts / max(_new_total, 1)) * 33), 90),
                f"Scraping recipe {attempts + 1} of {_new_total}: {_display}...",
            )
            detail = _scrape_recipe(url, user_agent)
            attempts += 1
            time.sleep(delay)
            if not detail:
                continue
            # Safety net: the URL should have been caught by all_known, but
            # race conditions / URL-normalisation edge cases can slip through.
            _existing = db.query(models.Recipe).filter(
                models.Recipe.source_url == url
            ).first()
            if _existing:
                log.warning("URL already in DB (id=%s) — updating instead of inserting: %s",
                            _existing.id, url)
                _update_row_from_detail(_existing, detail)
                db.add(_existing)
            else:
                row = models.Recipe(source_url=url, title=detail["name"], mealie_slug=None)
                _update_row_from_detail(row, detail)
                db.add(row)
                try:
                    db.flush()
                    new_recipes += 1
                except Exception as _flush_err:
                    # URL slipped through the dedup check (trailing-slash variant,
                    # race with background job, etc.) — roll back the pending add
                    # and treat as an update instead.
                    db.expunge(row)
                    _retry = db.query(models.Recipe).filter(
                        models.Recipe.source_url == url
                    ).first()
                    if _retry:
                        _update_row_from_detail(_retry, detail)
                        db.add(_retry)
                        log.debug("Flush race resolved — updated existing row for %s", url)
                    else:
                        log.warning("Flush failed and no existing row found for %s: %s", url, _flush_err)

        db.commit()
        duration = round(time.perf_counter() - t0, 1)
        stats = {
            "pages_fetched":    pages_fetched,
            "candidates":       len(candidate_urls),
            "stubs_refreshed":  stubs_refreshed,
            "scrape_attempts":  attempts,
            "new_recipes":      new_recipes,
            "duration_seconds": duration,
        }
        log.info("=== Scrape end | %s ===", stats)
        return stats
    finally:
        _scrape_lock.release()


def score_cached(
    household_id: str,
    db: Session,
    pantry_set: set[str],
    staples_set: set[str],
    prefs: Optional[models.Preference],
    weekly_hints: list[str],
    excluded_recipe_ids: set[str],
    max_results: int = 30,
) -> list[dict]:
    """
    Score every cached (non-rejected, non-imported) stub against the current
    pantry.  Pure CPU — no network I/O.  Uses precomputed ingredient tokens
    where available (Patch 12) so 10K stubs score in well under a second.

    Rating/review-count thresholds (min_scraped_rating, min_scraped_reviews)
    are applied here against each stub's stored scraped_rating/scraped_reviews
    (Patch 13), not at scrape time — so changing the threshold in Discovery
    Settings takes effect on the whole cached catalog on the very next
    suggest run, without waiting for a re-scrape.  A stub with no rating data
    yet (not backfilled by the background scraper) passes through rather
    than being silently dropped.
    """
    import json as _json
    t0 = time.perf_counter()

    cfg         = config_files.get_discovery_config()
    min_rating  = float(cfg.get("min_scraped_rating", 0) or 0)
    min_reviews = int(cfg.get("min_scraped_reviews", 0) or 0)

    stubs = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None),
        models.Recipe.source_url.isnot(None),
        models.Recipe.scraped_ingredients_json.isnot(None),
    ).all()

    scored: list[dict] = []
    excluded = 0
    below_rating = 0
    for stub in stubs:
        if stub.id in excluded_recipe_ids:
            excluded += 1
            continue
        if min_rating > 0 and stub.scraped_rating is not None and stub.scraped_rating < min_rating:
            below_rating += 1
            continue
        if min_reviews > 0 and stub.scraped_reviews is not None and stub.scraped_reviews < min_reviews:
            below_rating += 1
            continue
        try:
            ing_strings = _json.loads(stub.scraped_ingredients_json or "[]")
        except Exception:
            ing_strings = []
        if not ing_strings:
            continue
        try:
            raw_tokens = _json.loads(stub.scraped_tokens_json or "[]")
            # Clean legacy tokens that were stored before the unit-stripping
            # regex fix — removes "unit egg" → "egg", "garlic (tsp)" → "garlic"
            tokens = [_clean_stored_token(t) for t in raw_tokens if t]
        except Exception:
            tokens = []
        detail = {
            "name":               stub.title,
            "description":        stub.scraped_description or "",
            "tags":               [],
            "recipeIngredient":   [{"note": s} for s in ing_strings],
            "totalTime":          f"PT{stub.scraped_time_minutes}M" if stub.scraped_time_minutes else None,
            "_source_url":        stub.source_url,
            "_ingredient_tokens": tokens,
        }
        score, overlap_pct, missing = _score_scraped(
            detail, pantry_set, staples_set, prefs, weekly_hints,
        )
        if score == _HARD_REJECT:
            continue
        scored.append({
            "recipe_id":           stub.id,
            "title":               stub.title,
            "mealie_slug":         None,
            "source_url":          stub.source_url,
            "score":               round(score, 1),
            "pantry_overlap_pct":  round(overlap_pct * 100, 1),
            "missing_ingredients": missing[:15],
            "is_favorite":         False,
            "total_time_minutes":  stub.scraped_time_minutes,
            "_pending_import":     True,
        })

    scored.sort(key=lambda r: -r["score"])
    ms = (time.perf_counter() - t0) * 1000
    log.info(
        "score_cached: scored %d of %d stubs in %.0f ms (%d excluded, %d below rating≥%.1f★/reviews≥%d) "
        "— DB stub pool size is safe while this stays low",
        len(scored), len(stubs), ms, excluded, below_rating, min_rating, min_reviews,
    )
    return scored[:max_results]


def discover_and_score(
    household_id: str,
    week_start: date,
    db: Session,
    pantry_set: set[str],
    staples_set: set[str],
    prefs: Optional[models.Preference],
    weekly_hints: list[str],
    excluded_recipe_ids: set[str],
    max_results: int = 30,
) -> list[dict]:
    """
    Suggest-time entry point (Patch 12: cache-first).

    If the cache holds any stub scraped within stub_rescrape_days, scoring
    runs purely from the DB — no network I/O, returns in well under a second.
    Cold cache (first run after deploy / job disabled for a week) falls back
    to a synchronous scrape with the usual progress bar.
    """
    if not _SCRAPERS_AVAILABLE:
        log.warning("recipe-scrapers not installed — discovery disabled")
        return []

    cfg           = config_files.get_discovery_config()
    rescrape_days = int(cfg.get("stub_rescrape_days", 7))
    cutoff        = datetime.utcnow() - timedelta(days=rescrape_days)

    fresh_stubs = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None),
        models.Recipe.last_scraped_at.isnot(None),
        models.Recipe.last_scraped_at >= cutoff,
    ).count()

    log.info(
        "=== Discovery start | household=%s | week=%s | fresh stubs=%d ===",
        household_id, week_start, fresh_stubs,
    )

    if fresh_stubs == 0:
        log.info("Recipe cache is COLD — running synchronous scrape (progress bar shown)")
        set_progress(household_id, 2, "Recipe cache is cold — running full discovery...")
        collect_and_scrape(
            db,
            budget=int(cfg.get("max_scraped_per_run", 40)),
            progress_household=household_id,
        )
    else:
        log.info("Recipe cache is WARM — scoring from cache only")
        set_progress(household_id, 50, "Scoring cached recipes against your pantry...")

    set_progress(household_id, 93, "Ranking and filtering suggestions...")
    result = score_cached(
        household_id, db, pantry_set, staples_set, prefs, weekly_hints,
        excluded_recipe_ids, max_results,
    )
    set_progress(household_id, 100, "Done!")
    log.info(
        "=== Discovery end | returning %d | top score=%.1f ===",
        len(result), result[0]["score"] if result else 0.0,
    )
    return result
