"""
Recipe importer — reads a recipe from (almost) any web page and creates it in
Mealie, including pages Mealie's own URL importer can't read.

Mealie's importer (and recipe-scrapers) rely on schema.org Recipe data.  Many
sites — Wix/Squarespace blogs especially — have none; the recipe is just
headings, lists and paragraphs.  This module tries, in order, and keeps the
first result with at least 2 ingredients and 1 step:

  1. schema.org Recipe JSON-LD (handles @graph, lists, HowToSection)
  2. recipe-scrapers (site-specific rules, microdata, generic schema)
  3. Page layout — "Ingredients" / "Instructions|Method|Preparation" headings
     followed by lists/paragraphs, with sub-groups ("For the sauce")
  4. The local LLM (Ollama) reading the page text — last resort

The result is turned into a schema.org Recipe and sent to Mealie's
POST /api/recipes/create/html-or-json, so Mealie stores it natively and
downloads the photo itself.
"""
import json
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

UA_APP = "RecipePlanner/1.0 (personal household meal planner)"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class RecipeImportError(Exception):
    pass


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_html(url: str) -> tuple[str, str]:
    """Download the page (user-initiated, one request). Returns (html, final_url)."""
    last = None
    for ua in (UA_APP, UA_BROWSER):
        try:
            r = requests.get(url, timeout=25, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            last = f"HTTP {r.status_code}"
            if r.status_code == 200 and r.text:
                return r.text, r.url
            if r.status_code in (404, 410):
                raise RecipeImportError("That page doesn't exist (HTTP 404) — check the link.")
        except requests.RequestException as e:
            last = str(e)
    raise RecipeImportError(f"Couldn't download the page ({last}). The site may block automated requests — "
                            "open it in your browser and use Mealie's own import instead.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(s) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s.replace(" ", " ")


def _iso_minutes(v) -> Optional[int]:
    """'PT1H30M' / 'P0DT20M' / '45' → minutes."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v) or None
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", str(v).strip(), re.I)
    if m and any(m.groups()):
        d, h, mi = (int(x or 0) for x in m.groups())
        return d * 1440 + h * 60 + mi or None
    return _text_minutes(str(v))


def _text_minutes(s: str) -> Optional[int]:
    """'1 hour 30 minutes' / '2 hours' / '20 mins' → minutes."""
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", s, re.I)
    mi = re.search(r"(\d+)\s*(?:minutes?|mins?|m)\b", s, re.I)
    total = (float(h.group(1)) * 60 if h else 0) + (int(mi.group(1)) if mi else 0)
    return int(total) or None


def _first_image(v) -> Optional[str]:
    if isinstance(v, str):
        return v or None
    if isinstance(v, list):
        for x in v:
            u = _first_image(x)
            if u:
                return u
    if isinstance(v, dict):
        return v.get("url") or v.get("contentUrl")
    return None


def _empty() -> dict:
    return {"name": "", "description": "", "image": None, "yield": "",
            "prep_minutes": None, "cook_minutes": None, "total_minutes": None,
            "ingredient_groups": [], "instructions": [], "method": None}


def _n_ingredients(r: dict) -> int:
    return sum(len(g["items"]) for g in r.get("ingredient_groups", []))


def _good(r: Optional[dict]) -> bool:
    return bool(r) and _n_ingredients(r) >= 2 and len(r.get("instructions") or []) >= 1


# ── 1. JSON-LD ────────────────────────────────────────────────────────────────

def _walk(o):
    if isinstance(o, list):
        for x in o:
            yield from _walk(x)
    elif isinstance(o, dict):
        yield o
        for k in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement"):
            if k in o:
                yield from _walk(o[k])


def _instr_list(v) -> list[str]:
    out: list[str] = []
    if isinstance(v, str):
        parts = re.split(r"\n+|(?<=\.)\s{2,}", v)
        out += [_clean(p) for p in parts if _clean(p)]
    elif isinstance(v, list):
        for x in v:
            out += _instr_list(x)
    elif isinstance(v, dict):
        if v.get("itemListElement"):
            out += _instr_list(v["itemListElement"])
        elif v.get("text") or v.get("name"):
            out.append(_clean(v.get("text") or v.get("name")))
    return [s for s in out if s]


def from_jsonld(soup: BeautifulSoup) -> Optional[dict]:
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        for o in _walk(data):
            t = o.get("@type")
            if "Recipe" not in (t if isinstance(t, list) else [t]):
                continue
            r = _empty()
            r["name"] = _clean(o.get("name"))
            r["description"] = _clean(o.get("description"))
            r["image"] = _first_image(o.get("image"))
            y = o.get("recipeYield")
            r["yield"] = _clean(y[0] if isinstance(y, list) and y else y)
            r["prep_minutes"] = _iso_minutes(o.get("prepTime"))
            r["cook_minutes"] = _iso_minutes(o.get("cookTime"))
            r["total_minutes"] = _iso_minutes(o.get("totalTime"))
            ings = [_clean(i) for i in (o.get("recipeIngredient") or o.get("ingredients") or []) if _clean(i)]
            r["ingredient_groups"] = [{"title": "", "items": ings}] if ings else []
            r["instructions"] = _instr_list(o.get("recipeInstructions"))
            r["method"] = "recipe data on the page (JSON-LD)"
            return r
    return None


# ── 2. recipe-scrapers ────────────────────────────────────────────────────────

def from_recipe_scrapers(html: str, url: str) -> Optional[dict]:
    try:
        from recipe_scrapers import scrape_html
        s = scrape_html(html, org_url=url)
    except Exception:
        return None

    def g(fn, default=None):
        try:
            return getattr(s, fn)()
        except Exception:
            return default

    r = _empty()
    r["name"] = _clean(g("title", ""))
    r["description"] = _clean(g("description", ""))
    r["image"] = g("image")
    r["yield"] = _clean(g("yields", ""))
    r["prep_minutes"] = g("prep_time")
    r["cook_minutes"] = g("cook_time")
    r["total_minutes"] = g("total_time")
    groups = g("ingredient_groups") or []
    gl = []
    for grp in groups:
        items = [_clean(i) for i in (getattr(grp, "ingredients", None) or []) if _clean(i)]
        if items:
            gl.append({"title": _clean(getattr(grp, "purpose", "") or ""), "items": items})
    if not gl:
        ings = [_clean(i) for i in (g("ingredients") or []) if _clean(i)]
        gl = [{"title": "", "items": ings}] if ings else []
    r["ingredient_groups"] = gl
    r["instructions"] = [_clean(x) for x in (g("instructions_list") or []) if _clean(x)]
    r["method"] = "recipe-scrapers library"
    return r


# ── 3. Page layout ────────────────────────────────────────────────────────────

ING_HEAD = re.compile(r"^(ingredients?|ingredient list|what you('|’)ll need|you('|’)ll need|shopping list)\b[\s:]*$", re.I)
STEP_HEAD = re.compile(r"^(instructions?|directions?|method|preparation|prep(aration)? steps|steps|how to make( it| this)?|procedure|cooking instructions)\b[\s:]*$", re.I)
STOP_HEAD = re.compile(r"^(notes?|recipe notes|nutrition|tips?( & tricks)?|faqs?|frequently asked|comments?|related|you (may|might) also like|more recipes|equipment|recommended (tools?|equipment)|tools|serving suggestions?|storage|leave a (comment|reply)|reviews?|share|about (the|this) recipe)\b", re.I)
JUNK = re.compile(r"amazon\.|affiliate|subscribe|newsletter|sign up|thanks for submitting|cookie|privacy policy|"
                  r"all rights reserved|©|legal info|return policy|inquiries|pin it|jump to recipe|print recipe", re.I)
CHROME = ("footer", "nav", "aside", "form", "header")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _block_kind(el: Tag) -> str:
    if el.name in HEADINGS:
        return "heading"
    if el.name == "li":
        return "item"
    text = _clean(el.get_text(" "))
    bold = "".join(_clean(b.get_text(" ")) for b in el.find_all(["strong", "b"]))
    if text and len(text) <= 80 and bold and len(bold) >= len(text) * 0.8:
        return "label"
    return "para"


def _blocks(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Document-ordered (kind, tag, text) for headings, list items and paragraphs."""
    out = []
    for el in soup.find_all(list(HEADINGS) + ["p", "li"]):
        if el.name == "p" and el.find_parent("li"):
            continue                      # counted with its <li>
        if el.name == "li" and el.find("li"):
            continue                      # nested list container
        if el.find_parent(CHROME) or (el.get("role") or "") in ("navigation", "contentinfo"):
            continue                      # site header/footer/nav/sign-up forms
        text = _clean(el.get_text(" "))
        if not text or JUNK.search(text):
            continue
        kind = _block_kind(el)
        if out and out[-1][2] == text:
            continue
        out.append((kind, el.name, text))
    return out


def _looks_like_ingredient(s: str) -> bool:
    return len(s) <= 160 and bool(re.match(
        r"^([\d¼½¾⅓⅔⅛⅜⅝⅞/.\-–]+|a |an |one |two |three |juice of|zest of|pinch|handful|salt|pepper)", s, re.I))


def from_layout(soup: BeautifulSoup, text_for_meta: str) -> Optional[dict]:
    blocks = _blocks(soup)
    is_head = lambda k: k in ("heading", "label")
    i_ing = next((i for i, (k, _, t) in enumerate(blocks) if is_head(k) and ING_HEAD.match(t)), None)
    if i_ing is None:
        return None

    groups = [{"title": "", "items": []}]
    i = i_ing + 1
    while i < len(blocks):
        k, tag, t = blocks[i]
        if is_head(k) and (STEP_HEAD.match(t) or STOP_HEAD.match(t)):
            break
        if tag in ("h1", "h2") and not t.lower().startswith("for "):
            break
        if is_head(k):
            if not ING_HEAD.match(t):              # repeated "Ingredients:" label
                groups.append({"title": t.rstrip(":"), "items": []})
        elif k == "item" or (k == "para" and _looks_like_ingredient(t)):
            groups[-1]["items"].append(t)
        i += 1
    groups = [g for g in groups if g["items"]]

    steps: list[str] = []
    i_step = next((j for j in range(i, len(blocks)) if is_head(blocks[j][0]) and STEP_HEAD.match(blocks[j][2])), None)
    if i_step is not None:
        label = None
        for k, tag, t in blocks[i_step + 1:]:
            if is_head(k) and STOP_HEAD.match(t) and not (k == "label" and re.match(r"^tips?\b", t, re.I)):
                break
            if tag in ("h1", "h2"):
                break
            if is_head(k):
                if label:                         # label with no paragraph → its own step
                    steps.append(label)
                label = t.rstrip(":")
                continue
            step = re.sub(r"^(step\s*)?\d+[.):]\s*", "", t, flags=re.I)
            steps.append(f"{label}: {step}" if label else step)
            label = None
        if label:
            steps.append(label)

    r = _empty()
    r["ingredient_groups"] = groups
    r["instructions"] = steps
    r["method"] = "page layout (headings and lists)"
    return r


# ── Page metadata (title, photo, times, yield) ────────────────────────────────

def page_meta(soup: BeautifulSoup, text: str) -> dict:
    def meta(*names):
        for n in names:
            m = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if m and m.get("content"):
                return _clean(m["content"])
        return ""
    title = meta("og:title", "twitter:title") or (_clean(soup.title.string) if soup.title and soup.title.string else "")
    h1 = soup.find("h1")
    if h1 and _clean(h1.get_text(" ")):
        title = _clean(h1.get_text(" "))
    title = re.split(r"\s+[|–—]\s+", title)[0] if title else ""

    def grab(label_re):
        m = re.search(label_re + r"\s*:?\s*([^\n]{1,40})", text, re.I)
        return m.group(1) if m else ""
    ym = None
    for m in re.finditer(r"\b(?:yields?|servings?|serves|makes)\b\s*:?\s*", text, re.I):
        # look at the text after each label without consuming it, so an empty
        # "Serves:" label can't swallow the "Yields: 7-8 servings" that follows
        ym = re.match(r"(\d+(?:\s*(?:-|–|to)\s*\d+)?(?:\s*(?:servings?|people|portions))?)", text[m.end():m.end() + 40], re.I)
        if ym:
            break
    return {
        "name": title,
        "description": meta("og:description", "description"),
        "image": meta("og:image", "twitter:image") or None,
        "yield": _clean(ym.group(1)) if ym else "",
        "prep_minutes": _text_minutes(grab(r"\bprep(?:aration)?\s*time\b")),
        "cook_minutes": _text_minutes(grab(r"\bcook(?:ing)?\s*time\b")),
        "total_minutes": _text_minutes(grab(r"\btotal\s*time\b")),
    }


# ── 4. Local LLM ──────────────────────────────────────────────────────────────

def from_llm(text: str) -> Optional[dict]:
    from app import ollama_client
    if not ollama_client.is_configured():
        return None
    k = re.search(r"ingredients", text, re.I)
    start = max(0, (k.start() if k else 0) - 800)
    excerpt = text[start:start + 7000]
    prompt = (
        "Extract the recipe from this web page text. Copy ingredient lines and steps "
        "as written; do not invent anything. Return ONLY JSON: "
        '{"name": str, "servings": str, "prep_minutes": int|null, "cook_minutes": int|null, '
        '"ingredient_groups": [{"title": str, "items": [str]}], "instructions": [str]}\n\n'
        "PAGE TEXT:\n" + excerpt)
    # Writing out a whole recipe takes far longer than the shopping-list clean-up,
    # so this interactive import gets its own, longer limit.
    raw = ollama_client.generate(prompt, expect_json=True, timeout=240)
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    r = _empty()
    r["name"] = _clean(d.get("name"))
    r["yield"] = _clean(d.get("servings"))
    r["prep_minutes"] = d.get("prep_minutes") if isinstance(d.get("prep_minutes"), int) else None
    r["cook_minutes"] = d.get("cook_minutes") if isinstance(d.get("cook_minutes"), int) else None
    gl = []
    for g in d.get("ingredient_groups") or []:
        items = [_clean(x) for x in (g.get("items") or []) if _clean(x)] if isinstance(g, dict) else []
        if items:
            gl.append({"title": _clean(g.get("title")).rstrip(":"), "items": items})
    r["ingredient_groups"] = gl
    r["instructions"] = [_clean(x) for x in (d.get("instructions") or []) if _clean(x)]
    r["method"] = "local AI (Ollama) reading the page"
    return r


# ── Orchestration ─────────────────────────────────────────────────────────────

def parse_html(html: str, url: str, use_llm: bool = True) -> dict:
    """Extract a recipe from page HTML. Raises RecipeImportError if nothing usable."""
    soup = BeautifulSoup(html, "lxml")
    text_soup = BeautifulSoup(html, "lxml")
    for t in text_soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = "\n".join(l.strip() for l in text_soup.get_text("\n").split("\n") if l.strip())

    tried = []
    result = None
    for name, fn in (("json-ld", lambda: from_jsonld(soup)),
                     ("recipe-scrapers", lambda: from_recipe_scrapers(html, url)),
                     ("layout", lambda: from_layout(text_soup, text))):
        try:
            r = fn()
        except Exception as e:
            log.warning("Importer step %s failed: %s", name, e)
            r = None
        tried.append(name)
        if _good(r):
            result = r
            break
    if result is None and use_llm:
        tried.append("llm")
        r = from_llm(text)
        if _good(r):
            result = r
    if result is None:
        raise RecipeImportError(
            "Couldn't find a recipe on that page (tried: " + ", ".join(tried) + ").")

    meta = page_meta(soup, text)
    for k, v in meta.items():
        if not result.get(k):
            result[k] = v
    if not result.get("total_minutes") and (result.get("prep_minutes") or result.get("cook_minutes")):
        result["total_minutes"] = (result.get("prep_minutes") or 0) + (result.get("cook_minutes") or 0)
    result["source_url"] = url
    result["warnings"] = []
    if not result.get("image"):
        result["warnings"].append("No photo found on the page.")
    if not result.get("yield"):
        result["warnings"].append("Servings not found — add them so shopping-list scaling works.")
    log.info("Importer: '%s' via %s — %d ingredients, %d steps",
             result["name"], result["method"], _n_ingredients(result), len(result["instructions"]))
    return result


def read_recipe(url: str) -> dict:
    html, final_url = fetch_html(url)
    return parse_html(html, final_url)


# ── Save to Mealie ────────────────────────────────────────────────────────────

def _pt(minutes) -> Optional[str]:
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return f"PT{m // 60}H{m % 60}M" if m >= 60 else f"PT{m}M"


def to_schema_org(r: dict) -> dict:
    ings = [i for g in r.get("ingredient_groups", []) for i in g["items"]]
    d = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": r.get("name") or "Imported recipe",
        "description": r.get("description") or "",
        "recipeYield": r.get("yield") or "",
        "recipeIngredient": ings,
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in r.get("instructions", [])],
        "url": r.get("source_url"),
    }
    if r.get("image"):
        d["image"] = r["image"]
    for key, field in (("prepTime", "prep_minutes"), ("cookTime", "cook_minutes"), ("totalTime", "total_minutes")):
        v = _pt(r.get(field))
        if v:
            d[key] = v
    return d


def save_to_mealie(r: dict) -> tuple[str, bool]:
    """Create the recipe in Mealie. Returns (slug, already_existed)."""
    from app import mealie_client as mc
    mc._check_configured()
    url = r.get("source_url")
    if url:
        existing = mc.find_recipe_by_url(url)
        if existing:
            return existing, True

    html = ('<html><head><script type="application/ld+json">'
            + json.dumps(to_schema_org(r), ensure_ascii=False)
            + "</script></head><body></body></html>")
    try:
        resp = requests.post(f"{mc.MEALIE_BASE_URL}/api/recipes/create/html-or-json",
                             headers=mc._headers(), timeout=60,
                             json={"data": html, "url": url, "includeTags": False})
        resp.raise_for_status()
        slug = resp.json()
    except requests.RequestException as e:
        body = getattr(getattr(e, "response", None), "text", "")[:200]
        raise RecipeImportError(f"Mealie rejected the recipe: {e} {body}") from e
    if not isinstance(slug, str) or not slug:
        raise RecipeImportError(f"Unexpected Mealie response: {slug!r}")

    # Best effort: ingredient section titles and photo
    try:
        detail = mc.get_recipe(slug)
        changed = False
        groups = r.get("ingredient_groups", [])
        if len(groups) > 1 or (groups and groups[0].get("title")):
            idx = 0
            ing = detail.get("recipeIngredient") or []
            for g in groups:
                if g.get("title") and idx < len(ing):
                    ing[idx]["title"] = g["title"]
                    changed = True
                idx += len(g["items"])
        if url and not detail.get("orgURL"):
            detail["orgURL"] = url
            changed = True
        if changed:
            requests.patch(f"{mc.MEALIE_BASE_URL}/api/recipes/{slug}", headers=mc._headers(),
                           json=detail, timeout=30).raise_for_status()
        if r.get("image") and not detail.get("image"):
            requests.post(f"{mc.MEALIE_BASE_URL}/api/recipes/{slug}/image", headers=mc._headers(),
                          json={"url": r["image"]}, timeout=60).raise_for_status()
    except Exception as e:
        log.warning("Importer: post-create touch-ups for %s skipped: %s", slug, e)
    log.info("Importer: created Mealie recipe %s from %s", slug, url)
    return slug, False


def to_planner_detail(r: dict) -> dict:
    """Mealie-style detail dict for recipe_discovery._update_row_from_detail."""
    return {
        "name": r.get("name"),
        "description": r.get("description") or "",
        "recipeIngredient": [{"note": i} for g in r.get("ingredient_groups", []) for i in g["items"]],
        "recipeInstructions": [{"text": s} for s in r.get("instructions", [])],
        "totalTime": _pt(r.get("total_minutes")),
        "recipeServings": r.get("yield") or None,
        "nutrition": {},
    }
