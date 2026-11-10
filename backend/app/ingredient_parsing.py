"""
Mealie ingredient parsing (patch 151).

Mealie's URL / html-or-json importers store every ingredient as a plain note
(quantity 0, no unit, no food), so recipes show as "unparsed" and Mealie's
shopping list and scaling can't use them. This module runs Mealie's own NLP
parser (POST /api/parser/ingredients) on those lines, links the result to
Mealie foods/units (creating missing ones), and writes the recipe back.

Safety rules:
  • Only lines with no food are touched; already-parsed lines are left alone.
  • Lines below MIN_CONFIDENCE stay as plain notes (better unparsed than wrong).
  • A unit is only kept if it actually appears in the original text — the NLP
    model sometimes invents one (e.g. "1 pkg" → kilogram).
  • originalText, section titles and referenceId (used by step links) are kept.

Public API:
  parse_recipe(slug, dry_run=False)      → per-recipe summary dict
  audit_all()                            → counts across all Mealie recipes
  start_backfill(dry_run) / backfill_status()  → background run over all recipes
"""

import logging
import re
import threading
import time
from typing import Optional

import requests

from app import mealie_client as mc

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.7
_TIMEOUT = 60

# Extra spellings for common units, on top of Mealie's own name/abbreviation.
_UNIT_ALIASES = {
    "teaspoon": {"tsp", "tsps", "t", "teaspoons"},
    "tablespoon": {"tbsp", "tbsps", "tbs", "tbl", "tablespoons", "T"},
    "cup": {"cups", "c"},
    "pound": {"lb", "lbs", "pounds"},
    "ounce": {"oz", "ounces"},
    "gram": {"g", "grams", "gr"},
    "kilogram": {"kg", "kgs", "kilograms"},
    "milliliter": {"ml", "millilitre", "milliliters"},
    "liter": {"l", "litre", "liters", "litres"},
    "clove": {"cloves"},
    "pint": {"pints", "pt"},
    "quart": {"quarts", "qt"},
    "can": {"cans"},
    "package": {"pkg", "pkgs", "packages", "packet", "packets"},
    "pinch": {"pinches"},
    "dash": {"dashes"},
    "slice": {"slices"},
    "stick": {"sticks"},
}


_SUPER_SUB = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉⁄∕", "01234567890123456789//")
_VULGAR = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5",
           "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅙": "1/6", "⅚": "5/6", "⅛": "1/8",
           "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
_ABBR_PAREN = re.compile(
    r"\(\s*(?:tsp|tbsp|tbs|c|oz|fl\.? ?oz|lb|lbs|g|kg|ml|l|pt|qt)\.?\s*\)", re.I)


def _clean(text: str) -> str:
    """Normalise a line for Mealie's NLP parser.

    Strips markdown links and Budget Bytes-style prices, turns ¹/₂ and ½ into
    1/2 (the parser scores those below the confidence cut-off), drops
    HelloFresh-style unit echoes like "teaspoon (tsp)", and tidies whitespace.
    """
    t = (text or "").translate(_SUPER_SUB)
    for k, v in _VULGAR.items():
        t = re.sub(rf"(\d)\s*{k}", rf"\1 {v}", t)
        t = t.replace(k, v)
    t = _ABBR_PAREN.sub("", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\(\s*\$\d+(?:\.\d+)?\s*\)", "", t)          # "($1.12)"
    t = re.sub(r",?\s*\$\d+(?:\.\d+)?(?=\s*\))", "", t)       # "(full-fat, $1.12)"
    t = re.sub(r"\(\s*\)", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _line_text(ing: dict) -> str:
    return (ing.get("originalText") or ing.get("note") or ing.get("display") or "").strip()


def _is_parsed(ing: dict) -> bool:
    return bool(ing.get("food"))


def _is_ingredient_line(ing: dict) -> bool:
    """A row with text (section-title-only rows have none)."""
    return bool(_line_text(ing) or ing.get("food"))


def _unit_in_text(unit: dict, text: str) -> bool:
    words = set(re.findall(r"[A-Za-z]+", text))
    lower = {w.lower() for w in words}
    names = {unit.get(k) for k in ("name", "pluralName", "abbreviation", "pluralAbbreviation")}
    names = {n.strip() for n in names if n and n.strip()}
    base = (unit.get("name") or "").strip().lower()
    aliases = _UNIT_ALIASES.get(base, set()) | _UNIT_ALIASES.get(base.rstrip("s"), set())
    for n in names | aliases:
        if n == "T":                       # capital T = tablespoon only
            if "T" in words:
                return True
        elif n.lower() in lower:
            return True
    return False


# ── Mealie foods / units lookup with create-on-miss ───────────────────────────

class _Catalog:
    def __init__(self, kind: str):
        self.kind = kind            # "foods" | "units"
        self._by_name: Optional[dict] = None

    def _load(self) -> None:
        self._by_name = {}
        page = 1
        while True:
            r = requests.get(f"{mc.MEALIE_BASE_URL}/api/{self.kind}", headers=mc._headers(),
                             params={"page": page, "perPage": 500}, timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            for it in d.get("items", []):
                for k in ("name", "pluralName"):
                    if it.get(k):
                        self._by_name.setdefault(it[k].strip().lower(), it)
            if page >= (d.get("total_pages") or d.get("totalPages") or 1):
                break
            page += 1

    def resolve(self, obj: dict, create: bool) -> Optional[dict]:
        """Return {id, name} for a parser food/unit, creating it if missing."""
        if not obj or not (obj.get("name") or "").strip():
            return None
        if obj.get("id"):
            return obj
        if self._by_name is None:
            self._load()
        name = obj["name"].strip()
        hit = self._by_name.get(name.lower())
        if hit:
            return hit
        if not create:
            return {"id": None, "name": name}
        r = requests.post(f"{mc.MEALIE_BASE_URL}/api/{self.kind}", headers=mc._headers(),
                          json={"name": name}, timeout=_TIMEOUT)
        r.raise_for_status()
        new = r.json()
        self._by_name[name.lower()] = new
        log.info("Mealie: created %s '%s'", self.kind[:-1], name)
        return new


def _slim(obj: Optional[dict]) -> Optional[dict]:
    return {"id": obj["id"], "name": obj.get("name")} if obj and obj.get("id") else obj


def parse_recipe(slug: str, dry_run: bool = False, base_servings: Optional[float] = None,
                 _foods: Optional[_Catalog] = None, _units: Optional[_Catalog] = None) -> dict:
    """Parse the unparsed ingredient lines of one Mealie recipe."""
    mc._check_configured()
    foods = _foods or _Catalog("foods")
    units = _units or _Catalog("units")
    detail = mc.get_recipe(slug)
    ings = detail.get("recipeIngredient") or []
    lines = [i for i in ings if _is_ingredient_line(i)]
    summary = {"slug": slug, "name": detail.get("name"), "lines": len(lines),
               "parsed_before": sum(1 for i in lines if _is_parsed(i)),
               "newly_parsed": 0, "skipped": [], "changed": False, "dry_run": dry_run}

    todo = [(idx, i) for idx, i in enumerate(ings) if _is_ingredient_line(i) and not _is_parsed(i)]
    if not todo:
        summary["parsed_after"] = summary["parsed_before"]
        return summary

    texts = [_clean(_line_text(i)) for _, i in todo]
    r = requests.post(f"{mc.MEALIE_BASE_URL}/api/parser/ingredients", headers=mc._headers(),
                      json={"strategy": "nlp", "ingredients": texts}, timeout=_TIMEOUT)
    r.raise_for_status()
    results = r.json()

    for (idx, ing), text, res in zip(todo, texts, results):
        p = res.get("ingredient") or {}
        conf = (res.get("confidence") or {}).get("average") or 0
        food = p.get("food")
        if conf < MIN_CONFIDENCE or not food or not (food.get("name") or "").strip():
            summary["skipped"].append({"text": _line_text(ing), "confidence": round(conf, 2)})
            continue
        food = dict(food, name=food["name"].strip())
        unit = p.get("unit")
        if unit and not _unit_in_text(unit, text):
            unit = None
        new = dict(ing)
        new["quantity"] = p.get("quantity") or 0
        new["food"] = _slim(foods.resolve(food, create=not dry_run))
        new["unit"] = _slim(units.resolve(unit, create=not dry_run)) if unit else None
        new["note"] = (p.get("note") or "").strip(" ,")
        new["originalText"] = _line_text(ing)
        new.pop("display", None)
        ings[idx] = new
        summary["newly_parsed"] += 1

    summary["parsed_after"] = summary["parsed_before"] + summary["newly_parsed"]
    if summary["newly_parsed"] == 0 or dry_run:
        return summary

    detail["recipeIngredient"] = ings
    if base_servings and base_servings > 0 and not detail.get("recipeServings"):
        detail["recipeServings"] = base_servings
    w = requests.patch(f"{mc.MEALIE_BASE_URL}/api/recipes/{slug}", headers=mc._headers(),
                       json=detail, timeout=_TIMEOUT)
    if not w.ok:
        raise mc.MealieError(f"Mealie {w.status_code} saving '{slug}': {w.text[:300]}")

    # Confirm: read back and count what Mealie actually stored
    after = mc.get_recipe(slug).get("recipeIngredient") or []
    summary["parsed_after"] = sum(1 for i in after if _is_ingredient_line(i) and _is_parsed(i))
    summary["changed"] = True
    log.info("Parsed ingredients for '%s': %d/%d lines now linked (%d skipped)",
             slug, summary["parsed_after"], summary["lines"], len(summary["skipped"]))
    return summary


def parse_recipe_safe(slug: str, base_servings: Optional[float] = None) -> None:
    """Best-effort hook for import paths — never raises."""
    try:
        parse_recipe(slug, base_servings=base_servings)
    except Exception as e:
        log.warning("Ingredient parsing skipped for '%s': %s", slug, e)


# ── All-recipes audit and background backfill ─────────────────────────────────

def _all_slugs() -> list[str]:
    slugs, page = [], 1
    while True:
        r = requests.get(f"{mc.MEALIE_BASE_URL}/api/recipes", headers=mc._headers(),
                         params={"page": page, "perPage": 200}, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        slugs += [it["slug"] for it in d.get("items", [])]
        if page >= (d.get("total_pages") or d.get("totalPages") or 1):
            return slugs
        page += 1


def _classify(ings: list) -> str:
    lines = [i for i in ings if _is_ingredient_line(i)]
    if not lines:
        return "empty"
    n = sum(1 for i in lines if _is_parsed(i))
    if n == len(lines):
        return "parsed"
    return "partial" if n else "unparsed"


_state = {"running": False, "done": 0, "total": 0, "dry_run": False, "started": None,
          "finished": None, "results": [], "errors": [], "audit": None}
_lock = threading.Lock()


def audit_all() -> dict:
    mc._check_configured()
    counts = {"parsed": 0, "partial": 0, "unparsed": 0, "empty": 0}
    unparsed = []
    for slug in _all_slugs():
        d = mc.get_recipe(slug)
        k = _classify(d.get("recipeIngredient") or [])
        counts[k] += 1
        if k in ("partial", "unparsed"):
            unparsed.append({"slug": slug, "name": d.get("name"), "status": k})
    return {"total": sum(counts.values()), "counts": counts, "needs_parsing": unparsed}


def _run(dry_run: bool) -> None:
    foods, units = _Catalog("foods"), _Catalog("units")
    try:
        slugs = _all_slugs()
        with _lock:
            _state["total"] = len(slugs)
        for slug in slugs:
            try:
                s = parse_recipe(slug, dry_run=dry_run, _foods=foods, _units=units)
                if s["newly_parsed"] or s["skipped"]:
                    _state["results"].append(s)
            except Exception as e:
                _state["errors"].append(f"{slug}: {e}")
            _state["done"] += 1
            time.sleep(0.05)
        _state["audit"] = audit_all()
    except Exception as e:
        _state["errors"].append(f"run aborted: {e}")
    finally:
        _state["running"] = False
        _state["finished"] = time.time()


def start_backfill(dry_run: bool) -> dict:
    mc._check_configured()
    with _lock:
        if _state["running"]:
            return backfill_status()
        _state.update(running=True, done=0, total=0, dry_run=dry_run, started=time.time(),
                      finished=None, results=[], errors=[], audit=None)
    threading.Thread(target=_run, args=(dry_run,), daemon=True).start()
    return backfill_status()


def backfill_status() -> dict:
    s = dict(_state)
    s["recipes_changed"] = sum(1 for r in s["results"] if r["newly_parsed"])
    s["lines_parsed"] = sum(r["newly_parsed"] for r in s["results"])
    s["lines_skipped"] = sum(len(r["skipped"]) for r in s["results"])
    return s
