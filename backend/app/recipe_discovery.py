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

Flow (per weekly run)
----------------------
  1. HTML phase  — fetch category_urls per source, extract recipe links.
                   A robust exclusion filter strips pagination, category,
                   tag, auth, and other non-recipe URLs, and the non-dinner
                   keyword filter screens URL slugs, before candidates enter
                   the pool.
  2. Pool X      — re-scrape existing DB stubs (mealie_slug IS NULL, not
                   rejected) for fresh scoring.  Capped at max_scrape // 2.
  3. Pool Y      — scrape new URLs from phase 1.  Capped at max_scrape // 2.
  4. Quality gate — scraped pages exposing schema.org aggregateRating are
                   rejected if rating < min_scraped_rating OR
                   review_count < min_scraped_reviews.
                   Pages without schema ratings pass through.
  5. Score + return top max_results entries.

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
        if _same_host(full, base_url) and _looks_like_recipe_url(full):
            found.add(full)
    return list(found)


# ── Scraping ──────────────────────────────────────────────────────────────────

def _scrape_recipe(
    url: str,
    user_agent: str,
    min_rating: float,
    min_reviews: int,
    timeout: int = 20,
) -> Optional[dict]:
    """
    Scrape a single recipe URL using recipe-scrapers.

    Returns a Mealie-compatible detail dict if the recipe passes all quality
    filters, or None on failure / quality gate rejection.
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

        # ── Quality gate ──────────────────────────────────────────────────
        if min_rating > 0 or min_reviews > 0:
            try:
                raw_rating = scraper.ratings()
                raw_count  = scraper.ratings_count()

                if raw_rating is not None and raw_count is not None:
                    rating  = float(raw_rating)
                    reviews = int(raw_count)
                    if rating < min_rating or reviews < min_reviews:
                        log.debug(
                            "QUALITY SKIP %s — %.1f★/%d reviews (need ≥%.1f★/≥%d)",
                            url, rating, reviews, min_rating, min_reviews,
                        )
                        return None
                    log.debug("QUALITY OK %s — %.1f★ / %d reviews", url, rating, reviews)
                else:
                    log.debug("QUALITY N/A %s — no schema rating; editorial source, accepting", url)
            except Exception:
                log.debug("QUALITY N/A %s — rating check error; accepting", url)

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
            "Scraped OK '%s' — %d ingredients, time=%s (%s)",
            title, len(ingredients), total_time_raw or "?", url,
        )
        return {
            "name":             title,
            "description":      description,
            "tags":             tags_list,
            "recipeCategory":   [],
            "recipeIngredient": ingredients,
            "totalTime":        total_time_raw,
            "recipeServings":   yields_raw,
            "_source_url":      url,
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
    _qty_re = re.compile(
        r"^\s*[\d¼½¾⅓⅔⅛⅜⅝⅞\/\.\-]+\s*"
        r"(cups?|tbsp|tsp|tablespoons?|teaspoons?|lbs?|oz|g|kg|ml|l|"
        r"cloves?|heads?|bunches?|slices?|pieces?|cans?|packages?|"
        r"pounds?|ounces?|grams?|large|medium|small|whole|fresh|dried|"
        r"pinch(es)?|dash(es)?|handful)?\s*",
        re.IGNORECASE,
    )
    for ing in detail.get("recipeIngredient", []):
        raw = (ing.get("note", "") or "").strip() if isinstance(ing, dict) else ""
        if not raw:
            continue
        cleaned = _qty_re.sub("", raw).strip().lower().split(",")[0].strip()
        if cleaned:
            names.add(cleaned)
    return names


_PARSE_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?", re.IGNORECASE)


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
    ing_names   = _ingredient_names_from_text(detail)

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
    Full discovery pipeline.  Returns scored recipe dicts for the matching engine.
    """
    if not _SCRAPERS_AVAILABLE:
        log.warning("recipe-scrapers not installed — discovery disabled")
        return []

    cfg              = config_files.get_discovery_config()
    sources          = config_files.get_enabled_sources()
    delay            = float(cfg.get("request_delay_seconds", 2.0))
    user_agent       = str(cfg.get("user_agent", "RecipePlanner/1.0"))
    max_scrape       = int(cfg.get("max_scraped_per_run", 40))
    min_rating       = float(cfg.get("min_scraped_rating", 4.0))
    min_reviews      = int(cfg.get("min_scraped_reviews", 50))
    nd_keywords      = list(cfg.get("non_dinner_title_keywords", []))
    non_dinner_re    = _build_non_dinner_re(nd_keywords)
    rescrape_days    = int(cfg.get("stub_rescrape_days", 7))

    log.info(
        "=== Discovery start | household=%s | week=%s | max_scrape=%d | "
        "rating≥%.1f | reviews≥%d | %d sources | non-dinner filter: %d keywords ===",
        household_id, week_start, max_scrape, min_rating, min_reviews,
        len(sources), len(nd_keywords),
    )
    set_progress(household_id, 3, "Building recipe catalog...")

    # URLs already in Mealie — skip entirely
    mealie_imported_urls: set[str] = {
        r.source_url
        for r in db.query(models.Recipe).filter(
            models.Recipe.mealie_slug.isnot(None),
            models.Recipe.source_url.isnot(None),
        ).all()
    }

    # All stubs in DB — used for URL-level dedup regardless of rejection status.
    # A rejected recipe's URL must stay in all_known so Pool Y never tries to
    # re-insert it (UNIQUE constraint on source_url would fire otherwise).
    all_stubs = db.query(models.Recipe).filter(
        models.Recipe.mealie_slug.is_(None),
        models.Recipe.source_url.isnot(None),
    ).all()
    all_stub_urls  = {r.source_url for r in all_stubs}   # dedup — includes rejected
    all_known      = mealie_imported_urls | all_stub_urls

    # Pool X candidates — exclude rejected recipes from scoring, but NOT from dedup
    existing_stubs = [r for r in all_stubs if r.id not in excluded_recipe_ids]

    log.info(
        "Mealie-imported URLs: %d | all stubs: %d | Pool X eligible: %d",
        len(mealie_imported_urls), len(all_stubs), len(existing_stubs),
    )

    # ── Phase 1: collect URLs from HTML category / directory pages ────────────
    html_candidate_urls: list[str] = []
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    _total_pages = sum(len(s.get("category_urls", [])) for s in sources)
    _page_idx    = 0
    for source in sources:
        for cat_url in source.get("category_urls", []):
            _page_idx += 1
            _page_pct = 5 + int((_page_idx / max(_total_pages, 1)) * 40)
            set_progress(
                household_id, _page_pct,
                f"Fetching {source['name']} ({_page_idx} of {_total_pages} pages)...",
            )
            try:
                resp = requests.get(cat_url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    log.warning(
                        "Category page %s returned HTTP %s", cat_url, resp.status_code
                    )
                    continue
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

    log.info(
        "Candidate URLs: %d from HTML pages, %d after dedup",
        len(html_candidate_urls), len(candidate_urls),
    )

    random.shuffle(candidate_urls)

    # ── Scrape budget split ───────────────────────────────────────────────────
    stub_budget = max_scrape // 2
    new_budget  = max_scrape - stub_budget

    scored: list[dict] = []

    def _process(detail: dict, existing_row: Optional[models.Recipe]) -> Optional[dict]:
        score, overlap_pct, missing = _score_scraped(
            detail, pantry_set, staples_set, prefs, weekly_hints,
        )
        if score == _HARD_REJECT:
            return None

        import json as _json
        url = detail["_source_url"]
        ing_strings = [i.get("note", "") for i in detail.get("recipeIngredient", []) if isinstance(i, dict) and i.get("note")]

        if existing_row:
            recipe_id = existing_row.id
            # Refresh scraped data on live scrape (not called for cache hits)
            existing_row.scraped_ingredients_json = _json.dumps(ing_strings)
            existing_row.scraped_time_minutes     = _parse_minutes(detail.get("totalTime"))
            existing_row.scraped_description      = (detail.get("description") or "")[:500]
            existing_row.last_scraped_at          = datetime.utcnow()
            db.add(existing_row)
        else:
            # Safety net: check the DB before inserting.  The URL should have
            # been caught by all_known, but race conditions or URL-normalisation
            # edge cases can still slip through.  Treat an existing row as
            # existing_row so we update it rather than crash on the UNIQUE key.
            _existing = db.query(models.Recipe).filter(
                models.Recipe.source_url == url
            ).first()
            if _existing:
                log.warning(
                    "_process: URL already in DB (id=%s) — updating instead of inserting: %s",
                    _existing.id, url,
                )
                _existing.scraped_ingredients_json = _json.dumps(ing_strings)
                _existing.scraped_time_minutes     = _parse_minutes(detail.get("totalTime"))
                _existing.scraped_description      = (detail.get("description") or "")[:500]
                _existing.last_scraped_at          = datetime.utcnow()
                db.add(_existing)
                recipe_id = _existing.id
            else:
                row = models.Recipe(
                    source_url=url,
                    title=detail["name"],
                    mealie_slug=None,
                    scraped_ingredients_json=_json.dumps(ing_strings),
                    scraped_time_minutes=_parse_minutes(detail.get("totalTime")),
                    scraped_description=(detail.get("description") or "")[:500],
                    last_scraped_at=datetime.utcnow(),
                )
                db.add(row)
                db.flush()
                recipe_id = row.id

        if recipe_id in excluded_recipe_ids:
            return None

        log.debug(
            "SCORED %.1f | pantry=%.0f%% | missing=%d | '%s'",
            score, overlap_pct * 100, len(missing), detail.get("name", "?"),
        )
        return {
            "recipe_id":           recipe_id,
            "title":               detail["name"],
            "mealie_slug":         None,
            "source_url":          url,
            "score":               round(score, 1),
            "pantry_overlap_pct":  round(overlap_pct * 100, 1),
            "missing_ingredients": missing[:15],
            "is_favorite":         False,
            "total_time_minutes":  _parse_minutes(detail.get("totalTime")),
            "_pending_import":     True,
        }

    # ── Pool X: score existing stubs (scrape only if cache is stale) ────────
    # If a stub was scraped within stub_rescrape_days, score it from the
    # cached DB columns instead of hitting the network again.  Only stubs
    # older than the TTL (or never scraped) make a live HTTP request.
    set_progress(
        household_id, 47,
        f"Scoring {len(existing_stubs)} known recipes against your pantry...",
    )
    random.shuffle(existing_stubs)
    stub_scraped = stub_scored = stub_cached = 0
    cutoff = datetime.utcnow() - timedelta(days=rescrape_days)

    for stub in existing_stubs:
        if stub_scraped + stub_cached >= stub_budget:
            break

        # ── Use cached data if fresh enough ──────────────────────────────
        if stub.last_scraped_at and stub.last_scraped_at >= cutoff and stub.scraped_ingredients_json:
            import json as _json_cache
            try:
                ing_strings = _json_cache.loads(stub.scraped_ingredients_json or "[]")
            except Exception:
                ing_strings = []
            cached_detail = {
                "name":             stub.title,
                "description":      stub.scraped_description or "",
                "tags":             [],
                "recipeIngredient": [{"note": s} for s in ing_strings],
                "totalTime":        f"PT{stub.scraped_time_minutes}M" if stub.scraped_time_minutes else None,
                "_source_url":      stub.source_url,
            }
            entry = _process(cached_detail, existing_row=stub)
            stub_cached += 1
            log.debug("Pool X CACHE HIT: '%s' (scraped %s)", stub.title, stub.last_scraped_at.date())
            if entry:
                scored.append(entry)
                stub_scored += 1
            continue

        # ── Cache stale or missing — live scrape ─────────────────────────
        set_progress(
            household_id,
            48 + min(stub_scraped, 6),
            f"Re-scraping stale recipe: {stub.title[:50]}...",
        )
        detail = _scrape_recipe(stub.source_url, user_agent, min_rating, min_reviews)
        stub_scraped += 1
        time.sleep(delay)
        if not detail:
            continue
        entry = _process(detail, existing_row=stub)
        if entry:
            scored.append(entry)
            stub_scored += 1

    log.info(
        "Pool X (stubs): %d cache hits, %d scraped → %d scored",
        stub_cached, stub_scraped, stub_scored,
    )

    # ── Pool Y: scrape new URLs ───────────────────────────────────────────────
    new_scraped = new_scored = 0
    _pool_y_total = min(len(candidate_urls), new_budget)
    if _pool_y_total:
        set_progress(
            household_id, 57,
            f"Discovering new recipes (0 of {_pool_y_total})...",
        )
    for url in candidate_urls:
        if new_scraped >= new_budget or len(scored) >= max_results * 2:
            break
        _pool_y_pct = 57 + int((new_scraped / max(_pool_y_total, 1)) * 33)
        _display_url = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")[:55]
        set_progress(
            household_id, min(_pool_y_pct, 90),
            f"Scraping recipe {new_scraped + 1} of {_pool_y_total}: {_display_url}...",
        )
        detail = _scrape_recipe(url, user_agent, min_rating, min_reviews)
        new_scraped += 1
        time.sleep(delay)
        if not detail:
            continue
        entry = _process(detail, existing_row=None)
        if entry:
            scored.append(entry)
            new_scored += 1

    log.info("Pool Y (new):   scraped %d → %d scored", new_scraped, new_scored)
    set_progress(household_id, 93, "Ranking and filtering suggestions...")

    db.commit()

    scored.sort(key=lambda r: -r["score"])
    result = scored[:max_results]

    set_progress(household_id, 100, "Done!")
    log.info(
        "=== Discovery end | total scored=%d | returning %d | top score=%.1f ===",
        len(scored), len(result), result[0]["score"] if result else 0.0,
    )
    return result
