"""
Recipe Discovery Engine — Phase 7b
====================================

Phase 7b rewrites the URL-collection layer to use RSS / Atom feeds as the
primary mechanism, replacing HTML category-page crawling which proved
unreliable for most sources.

Why RSS beats category-page crawling
--------------------------------------
  • RSS feeds are designed for syndication — they are never bot-blocked.
  • WordPress feeds return the actual individual recipe URL in <link> tags;
    category-page crawling extracted sub-category index URLs instead.
  • Dotdash/Meredith sites (Serious Eats, Simply Recipes, AllRecipes, etc.)
    return 403 on every request — both category pages AND recipe pages —
    making HTML scraping impossible without a headless browser.

Flow (per weekly run)
----------------------
  1. RSS phase   — parse feed_urls from recipe_sources.yaml, collect
                   individual recipe URLs.  Paginate up to feed_pages deep.
  2. HTML phase  — fetch category_urls (fallback for non-WordPress sources).
                   A robust exclusion filter strips pagination, category,
                   tag, auth, and other non-recipe URLs before they enter
                   the candidate pool.
  3. Pool X      — re-scrape existing DB stubs (mealie_slug IS NULL, not
                   rejected) for fresh scoring.  Capped at max_scrape // 2.
  4. Pool Y      — scrape new URLs from phases 1+2.  Capped at
                   max_scrape // 2.
  5. Quality gate — scraped pages exposing schema.org aggregateRating are
                   rejected if rating < min_scraped_rating OR
                   review_count < min_scraped_reviews.
                   Editorial blogs without schema ratings pass through.
  6. Score + return top max_results entries.

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
from datetime import date
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

try:
    from recipe_scrapers import scrape_html
    from recipe_scrapers import SCRAPERS as SUPPORTED_SCRAPERS
    _SCRAPERS_AVAILABLE = True
except ImportError:
    _SCRAPERS_AVAILABLE = False
    SUPPORTED_SCRAPERS  = {}

from app import models, config_files

log = logging.getLogger(__name__)


# ── URL filtering ─────────────────────────────────────────────────────────────

# Patterns that definitively indicate a non-recipe page.
# Applied to BOTH RSS and HTML-scraped URLs.
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


def _clean_feed_url(url: str) -> str:
    """
    Strip query string and fragment from a feed entry URL.

    Many food blogs distribute their RSS feeds through mailing list services
    (ActiveCampaign, Mailchimp, ConvertKit) which append email-tracking
    parameters to every link, e.g.:
        https://www.skinnytaste.com/oven-fried-chicken/?adt_ei=*|EMAIL|*
        https://cookieandkate.com/peach-salad/?ck_subscriber_id=123456

    The base URL before the '?' is the canonical recipe page we want.
    Stripping the query string here means the validator and the scraper both
    see the clean URL, and the DB stub row stores the canonical URL (avoiding
    duplicates if the same recipe appears in a future feed page with a
    different tracking token).
    """
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def _is_valid_feed_url(url: str) -> bool:
    """
    Lightweight validator for URLs sourced from RSS / Atom feeds.

    Call _clean_feed_url() first to strip tracking query strings.

    RSS entries are curated content links — they don't need the same deep
    heuristics as HTML-scraped URLs.  We just verify the URL is HTTP(S), has
    at least one meaningful path segment, and doesn't match exclusion patterns.

    NOTE: Most WordPress recipe blogs use single-slug URLs like
    /chicken-parmesan/ — requiring 2+ segments incorrectly rejected all of them.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    # After _clean_feed_url() there should be no query string, but guard anyway
    if parsed.query:
        return False
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:          # bare domain — not a recipe page
        return False
    if _EXCLUDE_PATTERNS.search(path):
        return False
    return True


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


def _is_dinner_entry(entry, non_dinner_re: Optional[re.Pattern]) -> bool:
    """
    Returns True if an RSS entry looks like a dinner recipe.

    Checks the entry title and any RSS category/tag terms against the
    compiled non-dinner pattern.  Called BEFORE scraping so we don't
    waste scrape budget on pancakes and cheesecakes.
    """
    if non_dinner_re is None:
        return True   # filter disabled

    title = (getattr(entry, "title", "") or "").strip()
    # RSS category tags (feedparser stores them as entry.tags[].term)
    categories = " ".join(
        (c.get("term", "") if isinstance(c, dict) else str(c))
        for c in getattr(entry, "tags", [])
    )
    combined = f"{title} {categories}"

    m = non_dinner_re.search(combined)
    if m:
        log.debug("NON-DINNER filter: '%s' (matched '%s')", title, m.group(0))
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


# ── RSS / Atom feed fetching ──────────────────────────────────────────────────

def _fetch_feed_urls_with_entries(
    feed_url: str,
    user_agent: str,
    max_pages: int = 3,
) -> tuple[list, list[str]]:
    """
    Parse an RSS or Atom feed and return (entries, clean_urls) as parallel lists.

    Returning the feedparser entry objects alongside URLs allows callers to
    inspect entry.title and entry.tags for dinner pre-filtering without
    requiring an extra HTTP request.

    Paginates WordPress feeds via ?paged=N up to max_pages.
    Returns ([], []) if feedparser is not installed.
    """
    if not _FEEDPARSER_AVAILABLE:
        log.warning("feedparser not installed — RSS discovery unavailable for %s", feed_url)
        return [], []

    entries_out: list = []
    urls_out: list[str] = []

    for page in range(1, max_pages + 1):
        paged = f"{feed_url}?paged={page}" if page > 1 else feed_url
        try:
            feed = feedparser.parse(paged, agent=user_agent)

            if feed.bozo and not feed.entries:
                log.debug("Feed parse error for %s (page %d): %s", feed_url, page, feed.bozo_exception)
                break

            if not feed.entries:
                log.debug("Feed %s page %d has no entries — stopping pagination", feed_url, page)
                break

            page_pairs: list[tuple] = []
            rejected: list[str]    = []

            for entry in feed.entries:
                raw_link = getattr(entry, "link", None)
                if not raw_link:
                    continue
                # Strip email-tracking params (?adt_ei=*|EMAIL|*, ?ck_subscriber_id=…)
                link = _clean_feed_url(raw_link)
                if _is_valid_feed_url(link):
                    page_pairs.append((entry, link))
                else:
                    rejected.append(link)

            if rejected:
                log.debug(
                    "Feed %s page %d: %d URL-rejected (e.g. %s)",
                    feed_url, page, len(rejected), rejected[0],
                )
            log.debug(
                "Feed %s page %d: %d entries → %d valid URLs, %d rejected",
                feed_url, page, len(feed.entries), len(page_pairs), len(rejected),
            )

            for entry, url in page_pairs:
                entries_out.append(entry)
                urls_out.append(url)

            if len(feed.entries) < 10:
                break

        except Exception as e:
            log.warning("Feed fetch failed for %s (page %d): %s", feed_url, page, e)
            break

    return entries_out, urls_out


def _fetch_feed_urls(feed_url: str, user_agent: str, max_pages: int = 3) -> list[str]:
    """Convenience wrapper — returns only URLs (no entry objects)."""
    _, urls = _fetch_feed_urls_with_entries(feed_url, user_agent, max_pages)
    return urls


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
    feed_pages       = int(cfg.get("feed_pages", 4))
    user_agent       = str(cfg.get("user_agent", "RecipePlanner/1.0"))
    max_scrape       = int(cfg.get("max_scraped_per_run", 40))
    min_rating       = float(cfg.get("min_scraped_rating", 4.0))
    min_reviews      = int(cfg.get("min_scraped_reviews", 50))
    nd_keywords      = list(cfg.get("non_dinner_title_keywords", []))
    non_dinner_re    = _build_non_dinner_re(nd_keywords)

    log.info(
        "=== Discovery start | household=%s | week=%s | max_scrape=%d | "
        "rating≥%.1f | reviews≥%d | %d sources | non-dinner filter: %d keywords ===",
        household_id, week_start, max_scrape, min_rating, min_reviews,
        len(sources), len(nd_keywords),
    )

    # URLs already in Mealie — skip entirely
    mealie_imported_urls: set[str] = {
        r.source_url
        for r in db.query(models.Recipe).filter(
            models.Recipe.mealie_slug.isnot(None),
            models.Recipe.source_url.isnot(None),
        ).all()
    }

    # Existing stubs — re-scrape for fresh scoring each week
    existing_stubs = [
        r for r in db.query(models.Recipe).filter(
            models.Recipe.mealie_slug.is_(None),
            models.Recipe.source_url.isnot(None),
        ).all()
        if r.id not in excluded_recipe_ids
    ]
    stub_urls = {r.source_url for r in existing_stubs}
    all_known = mealie_imported_urls | stub_urls

    log.info(
        "Mealie-imported URLs: %d | existing stubs: %d",
        len(mealie_imported_urls), len(existing_stubs),
    )

    # ── Phase 1: collect URLs from RSS feeds ──────────────────────────────────
    feed_candidate_urls: list[str] = []
    for source in sources:
        for feed_url in source.get("feed_urls", []):
            log.info("Fetching RSS feed: %s (%s)", feed_url, source["name"])
            raw_entries, raw_urls = _fetch_feed_urls_with_entries(feed_url, user_agent, max_pages=feed_pages)
            # Apply dinner pre-filter against RSS entry titles/categories
            dinner_urls = []
            nd_skipped  = 0
            for entry, url in zip(raw_entries, raw_urls):
                if not _is_dinner_entry(entry, non_dinner_re):
                    nd_skipped += 1
                    continue
                dinner_urls.append(url)
            new = [u for u in dinner_urls if u not in all_known and u not in feed_candidate_urls]
            feed_candidate_urls.extend(new)
            log.info(
                "Feed '%s' — %s: %d URLs → %d dinner, %d non-dinner skipped, %d new",
                source["name"], feed_url, len(raw_urls), len(dinner_urls),
                nd_skipped, len(new),
            )
            time.sleep(delay * 0.5)

    # ── Phase 2: collect URLs from HTML category pages (fallback) ─────────────
    html_candidate_urls: list[str] = []
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for source in sources:
        for cat_url in source.get("category_urls", []):
            try:
                resp = requests.get(cat_url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    log.warning(
                        "Category page %s returned HTTP %s", cat_url, resp.status_code
                    )
                    continue
                found = _extract_recipe_urls(resp.text, cat_url)
                new   = [u for u in found if u not in all_known and u not in html_candidate_urls]
                html_candidate_urls.extend(new)
                log.info(
                    "HTML '%s' — %s: %d links extracted, %d new",
                    source["name"], cat_url, len(found), len(new),
                )
                time.sleep(delay)
            except Exception as e:
                log.warning("Category page failed (%s: %s): %s", source["name"], cat_url, e)

    # Combine: RSS first (higher quality), then HTML
    all_candidate_urls = feed_candidate_urls + html_candidate_urls
    # Deduplicate while preserving order
    seen: set[str] = set()
    candidate_urls: list[str] = []
    for u in all_candidate_urls:
        if u not in seen:
            seen.add(u)
            candidate_urls.append(u)

    log.info(
        "Candidate URLs: %d from RSS, %d from HTML, %d total after dedup",
        len(feed_candidate_urls), len(html_candidate_urls), len(candidate_urls),
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
            # Refresh scraped data on re-scrape
            existing_row.scraped_ingredients_json = _json.dumps(ing_strings)
            existing_row.scraped_time_minutes     = _parse_minutes(detail.get("totalTime"))
            existing_row.scraped_description      = (detail.get("description") or "")[:500]
            db.add(existing_row)
        else:
            row = models.Recipe(
                source_url=url,
                title=detail["name"],
                mealie_slug=None,
                scraped_ingredients_json=_json.dumps(ing_strings),
                scraped_time_minutes=_parse_minutes(detail.get("totalTime")),
                scraped_description=(detail.get("description") or "")[:500],
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

    # ── Pool X: re-scrape existing stubs ─────────────────────────────────────
    random.shuffle(existing_stubs)
    stub_scraped = stub_scored = 0
    for stub in existing_stubs:
        if stub_scraped >= stub_budget:
            break
        detail = _scrape_recipe(stub.source_url, user_agent, min_rating, min_reviews)
        stub_scraped += 1
        time.sleep(delay)
        if not detail:
            continue
        entry = _process(detail, existing_row=stub)
        if entry:
            scored.append(entry)
            stub_scored += 1

    log.info("Pool X (stubs): scraped %d → %d scored", stub_scraped, stub_scored)

    # ── Pool Y: scrape new URLs ───────────────────────────────────────────────
    new_scraped = new_scored = 0
    for url in candidate_urls:
        if new_scraped >= new_budget or len(scored) >= max_results * 2:
            break
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

    db.commit()

    scored.sort(key=lambda r: -r["score"])
    result = scored[:max_results]

    log.info(
        "=== Discovery end | total scored=%d | returning %d | top score=%.1f ===",
        len(scored), len(result), result[0]["score"] if result else 0.0,
    )
    return result
