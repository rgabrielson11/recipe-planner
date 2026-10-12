#!/usr/bin/env python3
"""
Recipe site vetter
==================
Tests every recipe-scrapers-supported site against the recipe-planner
discovery pipeline and reports which ones are viable recipe sources.

Runs inside the recipe-planner image (see run.sh) with the repo's
backend/app mounted at /app/app, so it uses the SAME recipe-scrapers
version and the app's OWN functions:
  rd._extract_recipe_urls  - link extraction from a category/listing page
  rd._scrape_recipe        - fetch + parse a single recipe
  rd._build_non_dinner_re  - the dinner filter from recipe_sources.yaml

Per site:
  1. Fetch homepage with the app's User-Agent (retry with a browser UA if blocked)
  2. Honour robots.txt for the app UA
  3. Find listing pages (dinner/main-dish/protein categories) and extract
     recipe links with the app's extractor; fall back to sitemaps
  4. Scrape a random sample of recipes and measure quality
  5. Verdict + family-dinner fit score

Output: /vet/out/results.json, /vet/out/report.md, /vet/out/suggested_sources.yaml

Usage: python /vet/vet_sites.py [--hosts a.com,b.com] [--sample 10] [--workers 16]
"""
import argparse
import json
import logging
import random
import re
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, "/app")
logging.basicConfig(level=logging.WARNING)

from recipe_scrapers import SCRAPERS  # noqa: E402
from app import recipe_discovery as rd  # noqa: E402
from app import config_files  # noqa: E402

OUT = "/vet/out"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
DELAY = 1.0            # seconds between requests to the same host
HOST_DEADLINE = 180    # max seconds spent sampling recipes per host
MAX_LISTINGS = 5       # listing pages fetched per host
MAX_ATTEMPTS = 25      # max recipe links tried per host
US_TLDS = {"com", "org", "net", "gov", "info", "co", "us"}   # skip .de/.uk/.au etc.


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


_cfg = config_files.load_yaml("recipe_sources.yaml") or {}
APP_UA = _find_key(_cfg, "user_agent") or "RecipePlanner/1.0 (personal household meal planner)"
NON_DINNER_RE = rd._build_non_dinner_re(list(_find_key(_cfg, "non_dinner_title_keywords") or []))
CONFIGURED_URLS = {}
for _s in (_find_key(_cfg, "sources") or []):
    for _u in (_s.get("category_urls") or []):
        CONFIGURED_URLS.setdefault(urlparse(_u).netloc.replace("www.", ""), []).append(_u)
CONFIGURED = set(CONFIGURED_URLS)

# ── HTTP ──────────────────────────────────────────────────────────────────────
BLOCK_MARKERS = ("just a moment", "cf-chl", "captcha", "access denied",
                 "attention required", "are you a robot", "px-captcha")


def fetch(url, ua, timeout=20):
    h = {"User-Agent": ua,
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text, r.url
    except Exception as e:
        return None, str(e)[:200], url


def is_blocked(status, text):
    if status in (401, 403, 429, 503):
        return True
    head = (text or "")[:6000].lower()
    return status == 200 and any(m in head for m in BLOCK_MARKERS) and len(text) < 60000


# ── Listing discovery ─────────────────────────────────────────────────────────
DINNER_HINT = re.compile(r"dinner|main[-_]?(dish|course)|entree|weeknight|family|supper", re.I)
PROTEIN_HINT = re.compile(r"chicken|beef|pork|seafood|fish|pasta|turkey|shrimp|salmon|vegetarian", re.I)
LISTING_SHAPE = re.compile(r"/(category|categories|course|courses|recipes|recipe-index|"
                           r"collections?|tag|tags|topics?|meal-type|meals|cuisine|c)/|"
                           r"recipe-index|/recipes/?$", re.I)
JUNK = re.compile(r"dessert|breakfast|brunch|drink|cocktail|baking|cookie|cake|snack|"
                  r"appetizer|holiday|christmas|thanksgiving|login|account|shop|privacy", re.I)
COMMON_PATHS = ["/recipes/dinner/", "/category/dinner/", "/category/recipes/main-dish/",
                "/category/main-dishes/", "/dinner/", "/recipes/", "/recipe-index/"]


def listing_candidates(home_html, home_url):
    soup = BeautifulSoup(home_html, "lxml")
    scored = {}
    for a in soup.find_all("a", href=True):
        full = urljoin(home_url, a["href"].split("#")[0])
        p = urlparse(full)
        if p.netloc != urlparse(home_url).netloc or JUNK.search(p.path):
            continue
        text = p.path + " " + a.get_text(" ", strip=True)
        s = 0
        if DINNER_HINT.search(text):
            s += 3
        if PROTEIN_HINT.search(text):
            s += 1
        if LISTING_SHAPE.search(p.path):
            s += 2
        if s >= 2:
            scored[full.rstrip("/") + "/"] = max(s, scored.get(full, 0))
    ranked = sorted(scored, key=lambda u: -scored[u])
    base = f"{urlparse(home_url).scheme}://{urlparse(home_url).netloc}"
    for cp in COMMON_PATHS:
        u = base + cp
        if u not in ranked:
            ranked.append(u)
    return ranked


def sitemap_urls(base, ua, robots_txt):
    maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt or "")
    maps += [base + "/sitemap_index.xml", base + "/sitemap.xml", base + "/wp-sitemap.xml"]
    seen, urls = set(), []
    fetched = 0
    queue = list(dict.fromkeys(maps))
    while queue and fetched < 4 and len(urls) < 400:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        time.sleep(DELAY)
        st, txt, _ = fetch(sm, ua)
        fetched += 1
        if st != 200 or "<" not in (txt or ""):
            continue
        try:
            root = ET.fromstring(txt.encode("utf-8", "ignore"))
        except ET.ParseError:
            continue
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        if root.tag.endswith("sitemapindex"):
            pri = [l for l in locs if re.search(r"recipe", l, re.I)]
            pri += [l for l in locs if re.search(r"post", l, re.I) and l not in pri]
            queue = pri[:3] + queue
        else:
            urls += [l for l in locs if rd._looks_like_recipe_url(l.rstrip("/"))]
    return urls


# ── Recipe metrics ────────────────────────────────────────────────────────────
QTY_RE = re.compile(r"^\s*(\d|[¼½¾⅓⅔⅛⅜⅝⅞]|(a|an|one|two|three|four|half)\s)", re.I)
EN_WORDS = re.compile(r"\b(cups?|tablespoons?|teaspoons?|tbsp|tsp|ounces?|oz|pounds?|lbs?|"
                      r"cloves?|salt|pepper|chopped|minced|sliced|diced|and|fresh|large|"
                      r"medium|can|or|to taste|grated|olive oil|butter)\b", re.I)
US_UNITS = re.compile(r"\b(cups?|tablespoons?|teaspoons?|tbsp|tsp|ounces?|oz|pounds?|lbs?)\b", re.I)
METRIC = re.compile(r"\d\s*(g|kg|ml|l|gr)\b", re.I)


def minutes(pt):
    if not pt:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", pt)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) or None


def recipe_metrics(d):
    notes = [i.get("note") or "" for i in d.get("recipeIngredient", [])]
    joined = " ".join(notes)
    title = d.get("name") or ""
    serv = d.get("recipeServings")
    return {
        "title": title,
        "url": d.get("_source_url"),
        "n_ing": len(notes),
        "qty_rate": (sum(1 for n in notes if QTY_RE.match(n)) / len(notes)) if notes else 0,
        "servings": bool(serv and re.search(r"\d", str(serv))),
        "minutes": minutes(d.get("totalTime")),
        "steps": len(d.get("recipeInstructions") or []),
        "english": len(EN_WORDS.findall(joined)) >= 3,
        "us_units": bool(US_UNITS.search(joined)) and not len(METRIC.findall(joined)) > 2,
        "dinner": not (NON_DINNER_RE and NON_DINNER_RE.search(title)),
        "rating": d.get("_rating"),
        "reviews": d.get("_reviews"),
    }


# ── Per-host vetting ──────────────────────────────────────────────────────────
def vet_host(host, sample_n):
    res = {"host": host, "verdict": None, "reasons": [], "ua": "app",
           "configured": host.replace("www.", "") in CONFIGURED}
    base = f"https://{host}"

    # 1. homepage
    st, html, final = fetch(base + "/", APP_UA)
    if st is None:
        st, html, final = fetch(f"https://www.{host}/", APP_UA) if not host.startswith("www.") else (st, html, final)
    if st is None:
        res.update(verdict="unreachable", reasons=[html])
        return res
    if is_blocked(st, html):
        time.sleep(DELAY)
        st2, html2, final2 = fetch(base + "/", BROWSER_UA)
        if st2 == 200 and not is_blocked(st2, html2):
            res["ua"] = "browser"
            st, html, final = st2, html2, final2
            res["reasons"].append("app User-Agent blocked; browser UA works")
        else:
            res.update(verdict="blocked", reasons=[f"HTTP {st} / bot wall for both UAs"])
            return res
    if st != 200:
        res.update(verdict="unreachable", reasons=[f"homepage HTTP {st}"])
        return res
    ua = APP_UA if res["ua"] == "app" else BROWSER_UA
    p = urlparse(final)
    base = f"{p.scheme}://{p.netloc.replace(':443', '')}"
    res["final_base"] = base

    # 2. robots.txt
    time.sleep(DELAY)
    rst, robots_txt, _ = fetch(base + "/robots.txt", ua, timeout=10)
    rp = robotparser.RobotFileParser()
    rp.parse((robots_txt or "").splitlines() if rst == 200 else [])

    # 3. listing discovery with the app's extractor
    best = (None, [])
    tried = []
    configured = CONFIGURED_URLS.get(host.replace("www.", ""), [])
    candidates = configured[:MAX_LISTINGS] or listing_candidates(html, base + "/")[:MAX_LISTINGS + 3]
    for cand in candidates:
        if len(tried) >= MAX_LISTINGS:
            break
        time.sleep(DELAY)
        cst, chtml, cfinal = fetch(cand, ua)
        if cst != 200 or is_blocked(cst, chtml):
            continue
        links = [u for u in rd._extract_recipe_urls(chtml, cfinal)
                 if rd._is_dinner_url(u, NON_DINNER_RE)]
        tried.append({"url": cand, "links": len(links)})
        def _lscore(u, n):
            return n + (15 if DINNER_HINT.search(u) else 0) + (20 if LISTING_SHAPE.search(urlparse(u).path) else 0)
        dinnerish = bool(DINNER_HINT.search(cand))
        score = _lscore(cand, len(links))
        best_score = _lscore(best[0], len(best[1])) if best[0] else -1
        if links and score > best_score:
            best = (cand, links)
        if dinnerish and LISTING_SHAPE.search(urlparse(cand).path) and len(links) >= 12:
            break
    res["listings_tried"] = tried
    links = best[1]
    if len(links) >= 5:
        res["discovery"] = "listing"
        res["listing_url"] = best[0]
    else:
        sm = sitemap_urls(base, ua, robots_txt if rst == 200 else "")
        sm = [u for u in sm if rd._is_dinner_url(u, NON_DINNER_RE)]
        if len(sm) >= 5:
            res["discovery"] = "sitemap"
            res["reasons"].append("no usable HTML listing page; recipes only found via sitemap")
            links = sm
        else:
            res.update(verdict="no-recipes-found",
                       reasons=res["reasons"] + ["no listing page or sitemap yielded recipe links"])
            return res
    res["links_found"] = len(links)

    # robots check on recipe paths
    sample = list(links)
    random.shuffle(sample)
    if not all(rp.can_fetch(APP_UA, u) for u in sample[:3]):
        res.update(verdict="robots-disallowed", reasons=res["reasons"] + ["robots.txt disallows recipe pages"])
        return res

    # 4. scrape the sample with the app's own scraper
    t0 = time.time()
    recipes, attempted = [], 0
    for u in sample:
        # keep sampling until we have sample_n real recipes, max MAX_ATTEMPTS links
        if time.time() - t0 > HOST_DEADLINE or len(recipes) >= sample_n or attempted >= MAX_ATTEMPTS:
            break
        time.sleep(DELAY)
        attempted += 1
        d = rd._scrape_recipe(u, ua)
        if d:
            recipes.append(recipe_metrics(d))
    res["attempted"] = attempted
    res["parsed"] = len(recipes)
    res["precision"] = round(len(recipes) / attempted, 2) if attempted else 0
    res["samples"] = recipes

    if len(recipes) < 3:
        res.update(verdict="few-recipe-links",
                   reasons=res["reasons"] + [f"listing pages mostly link to non-recipe pages ({len(recipes)}/{attempted} sampled links were recipes)"])
        return res

    # 5. aggregate
    def rate(k):
        return round(sum(1 for r in recipes if r[k]) / len(recipes), 2)
    mins = [r["minutes"] for r in recipes if r["minutes"]]
    ratings = [r["rating"] for r in recipes if r["rating"] is not None]
    agg = {
        "qty_rate": round(statistics.mean(r["qty_rate"] for r in recipes), 2),
        "servings_rate": rate("servings"),
        "time_rate": round(len(mins) / len(recipes), 2),
        "english_rate": rate("english"),
        "us_units_rate": rate("us_units"),
        "dinner_rate": rate("dinner"),
        "median_minutes": int(statistics.median(mins)) if mins else None,
        "median_ingredients": int(statistics.median(r["n_ing"] for r in recipes)),
        "rating_rate": round(len(ratings) / len(recipes), 2),
        "median_rating": round(statistics.median(ratings), 2) if ratings else None,
    }
    res.update(agg)

    if agg["english_rate"] < 0.5:
        res["verdict"] = "non-english"
        return res
    if agg["us_units_rate"] < 0.5:
        res["verdict"] = "non-us"
        res["reasons"].append(f"mostly metric/non-US recipes ({int(agg['us_units_rate']*100)}% US units)")
        return res
    quality_ok = agg["qty_rate"] >= 0.7 and agg["servings_rate"] >= 0.6
    if not quality_ok:
        res["verdict"] = "low-quality"
        if agg["qty_rate"] < 0.7:
            res["reasons"].append(f"only {int(agg['qty_rate']*100)}% of ingredient lines have quantities")
        if agg["servings_rate"] < 0.6:
            res["reasons"].append("servings often missing (breaks shopping-list scaling)")
    elif res["discovery"] == "listing" and res["ua"] == "app" and res["precision"] >= 0.5:
        res["verdict"] = "viable"
    else:
        res["verdict"] = "viable-with-changes"
        if res["precision"] < 0.5:
            res["reasons"].append(f"noisy links: only {int(res['precision']*100)}% of extracted links were "
                                  "recipes (rest are nav/category pages) - wastes scrape budget")

    # family-dinner fit (0-100)
    mm = agg["median_minutes"]
    fit = agg["dinner_rate"] * 40
    fit += 25 if (mm and mm <= 45) else 15 if (mm and mm <= 60) else 5 if mm else 8
    fit += 15 if 6 <= agg["median_ingredients"] <= 16 else 5
    fit += agg["us_units_rate"] * 10
    fit += 10 if agg["rating_rate"] >= 0.5 else 0
    res["fit"] = int(round(fit))
    return res


# ── Reporting ─────────────────────────────────────────────────────────────────
ORDER = ["viable", "viable-with-changes", "low-quality", "few-recipe-links", "no-recipes-found",
         "robots-disallowed", "blocked", "non-us", "non-english", "unreachable", "error"]


def write_outputs(results, started):
    results = sorted(results, key=lambda r: (ORDER.index(r["verdict"]) if r["verdict"] in ORDER else 99,
                                             -(r.get("fit") or 0), r["host"]))
    with open(f"{OUT}/results.json", "w") as f:
        json.dump({"started": started, "app_ua": APP_UA, "results": results}, f, indent=2, default=str)

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    L = [f"# Recipe site vetting report", "", f"Run started {started}. {len(results)} sites tested.", "",
         "| Verdict | Sites |", "|---|---|"]
    L += [f"| {v} | {counts[v]} |" for v in ORDER if v in counts]
    for v in ("viable", "viable-with-changes"):
        rows = [r for r in results if r["verdict"] == v]
        if not rows:
            continue
        L += ["", f"## {v} ({len(rows)})", "",
              "| Site | Fit | Parsed | Qty% | Serv% | Dinner% | Med min | Med ing | US units | Rating | Listing / notes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            note = r.get("listing_url") or ""
            if r["reasons"]:
                note = (note + " — " if note else "") + "; ".join(r["reasons"])
            if r.get("configured"):
                note = "**already configured** " + note
            L.append(f"| {r['host']} | {r.get('fit')} | {r['parsed']}/{r['attempted']} | "
                     f"{int(r['qty_rate']*100)} | {int(r['servings_rate']*100)} | {int(r['dinner_rate']*100)} | "
                     f"{r.get('median_minutes') or '-'} | {r['median_ingredients']} | "
                     f"{int(r['us_units_rate']*100)}% | {r.get('median_rating') or '-'} | {note} |")
            titles = [s["title"] for s in r["samples"]][:5]
            L.append(f"| | | | | | | | | | | _e.g. {' · '.join(titles)}_ |")
    L += ["", "## Not viable", "", "| Site | Verdict | Reason |", "|---|---|---|"]
    for r in results:
        if r["verdict"] not in ("viable", "viable-with-changes"):
            L.append(f"| {r['host']} | {r['verdict']} | {'; '.join(r['reasons'])[:160]} |")
    with open(f"{OUT}/report.md", "w") as f:
        f.write("\n".join(L) + "\n")

    Y = ["# Paste-ready entries for recipe_sources.yaml (viable sites, best family fit first)", ""]
    for r in [r for r in results if r["verdict"] == "viable" and not r.get("configured")]:
        Y += [f"- name: {r['host']}", "  enabled: false",
              f"  notes: \"Vetter: fit {r.get('fit')}, {r['parsed']}/{r['attempted']} parsed, "
              f"median {r.get('median_minutes') or '?'} min\"",
              "  category_urls:", f"  - {r['listing_url']}", ""]
    with open(f"{OUT}/suggested_sources.yaml", "w") as f:
        f.write("\n".join(Y))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", default="")
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--all-regions", action="store_true",
                    help="include non-US country-TLD sites (default: US/generic TLDs only)")
    a = ap.parse_args()
    hosts = [h.strip() for h in a.hosts.split(",") if h.strip()] or sorted(
        h for h in SCRAPERS if a.all_regions or h.rsplit(".", 1)[-1] in US_TLDS)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Vetting {len(hosts)} hosts | app UA: {APP_UA} | sample {a.sample} | workers {a.workers}", flush=True)
    results, lock = [], threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(vet_host, h, a.sample): h for h in hosts}
        for fut in as_completed(futs):
            h = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"host": h, "verdict": "error", "reasons": [repr(e)[:200]]}
            with lock:
                results.append(r)
                done += 1
                print(f"[{done}/{len(hosts)}] {h}: {r['verdict']}"
                      f"{' fit=' + str(r['fit']) if r.get('fit') is not None else ''}", flush=True)
                if done % 25 == 0:
                    write_outputs(results, started)
    write_outputs(results, started)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
