"""
Shopping List Generator — Phase 5

Builds a pantry-first, waste-minimising shopping list from the household's
confirmed WeeklySelections for the given week.

Pipeline:
  1. Fetch full ingredient details from Mealie for each selected recipe
  2. Scale all quantities to household size (recipe yield → num_people)
  3. Aggregate totals across all recipes (sum first, round once)
  4. Subtract tracked pantry on-hand quantities (same unit, quantity-aware)
  5. Separate staples into their own PANTRY CHECK section — assumed on hand
     but listed with required quantities as a final verify-before-you-shop
     reminder. They are NOT in the main buy list.
  6. Round remaining BUY quantities up to real package sizes
  7. Group main list by store section

Output sections:
  shopping_by_section  → items to buy, grouped by store section
  pantry_check         → staples needed this week (assumed on hand, just verify)
  using_from_pantry    → tracked pantry items being consumed
  warnings             → unit mismatches, missing Mealie data, etc.
"""

import json
import logging
import math
import re
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app import models, mealie_client, config_files, ingredient_utils

log = logging.getLogger(__name__)

# ── Store section keywords ────────────────────────────────────────────────────
# (Patch 15: dropped the unused _guess_section() duplicate of _section_for() —
# scraped ingredients are now sectioned the same way as everything else, via
# _section_for() on the final aggregated/normalized name.)
SECTION_KEYWORDS: dict[str, list[str]] = {
    "produce": [
        "lettuce", "spinach", "kale", "arugula", "cabbage", "bok choy",
        "broccoli", "cauliflower", "carrot", "celery", "cucumber", "zucchini",
        "squash", "eggplant", "pepper", "bell pepper", "jalapeño", "chili",
        "tomato", "onion", "shallot", "scallion", "green onion", "leek",
        "garlic", "ginger", "potato", "sweet potato", "corn",
        "mushroom", "asparagus", "green bean", "pea", "edamame", "avocado",
        "lemon", "lime", "orange", "apple", "berry",
        "cilantro", "parsley", "basil", "thyme", "rosemary", "mint", "dill",
        "herb", "fresh herb",
    ],
    "meat & seafood": [
        "chicken", "beef", "pork", "lamb", "turkey", "duck",
        "salmon", "shrimp", "tuna", "cod", "tilapia", "halibut", "scallop",
        "crab", "lobster", "clam", "mussel", "oyster", "anchovy",
        "bacon", "sausage", "ham", "prosciutto", "chorizo",
        "ground beef", "ground pork", "ground turkey", "steak",
    ],
    "dairy & eggs": [
        "milk", "cream", "half and half", "butter", "egg",
        "cheese", "parmesan", "mozzarella", "cheddar", "feta", "ricotta",
        "yogurt", "sour cream", "cream cheese", "cottage cheese",
    ],
    "bread & bakery": [
        "bread", "tortilla", "pita", "baguette", "roll", "bun",
        "naan", "wrap", "crouton", "breadcrumb", "panko",
    ],
    "canned & jarred": [
        "canned tomato", "tomato paste", "tomato sauce", "salsa",
        "canned bean", "chickpea", "lentil", "black bean", "kidney bean",
        "coconut milk", "broth", "stock",
        "olive", "pickle", "capers", "artichoke heart", "roasted pepper",
    ],
    "dry goods & pasta": [
        "pasta", "spaghetti", "penne", "fettuccine", "linguine", "rigatoni",
        "rice", "quinoa", "couscous", "orzo", "farro", "barley",
        "cornmeal", "oat", "dried bean", "split pea",
    ],
    "oils, sauces & condiments": [
        "oil", "vinegar", "soy sauce", "fish sauce", "oyster sauce",
        "hot sauce", "sriracha", "worcestershire", "mustard", "ketchup",
        "mayo", "mayonnaise", "tahini", "miso", "hoisin",
    ],
    "spices & baking": [
        "cumin", "paprika", "turmeric", "coriander", "cinnamon", "oregano",
        "thyme", "bay leaf", "chili powder", "cayenne", "nutmeg", "clove",
        "brown sugar", "honey", "maple syrup", "vanilla",
        "baking soda", "baking powder", "cornstarch", "yeast",
    ],
    "frozen": [
        "frozen", "ice cream", "frozen pea", "frozen corn", "frozen edamame",
    ],
    "beverages": [
        "wine", "beer", "juice",
    ],
}


def _section_for(name: str) -> str:
    n = name.lower()
    for section, keywords in SECTION_KEYWORDS.items():
        if any(kw in n for kw in keywords):
            return section
    return "other"


# ── Unit normalisation ────────────────────────────────────────────────────────
# Moved to ingredient_utils.py (Patch 15) so the shopping-list unit-conversion
# helpers (to_base/from_base/unit_family) share one canonicalisation table
# instead of drifting out of sync. Kept as a local alias for minimal diff.
_canonical_unit = ingredient_utils.canonical_unit


# ── Mealie ingredient extraction ──────────────────────────────────────────────

def _parse_servings(detail: dict) -> Optional[float]:
    raw = detail.get("recipeServings") or detail.get("recipeYield")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw))
    return float(m.group(1)) if m else None


def _extract_ingredients(detail: dict) -> list[dict]:
    ingredients = []
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict):
            continue
        food = ing.get("food")
        raw_name = (
            food.get("name", "") if isinstance(food, dict)
            else str(food) if food
            else ing.get("note", "")
        )
        raw_name = raw_name.strip()
        if not raw_name:
            continue
        # Patch 15: normalize so "Yellow Onion", "yellow onions", and
        # "yellow onion, diced" all collapse to the same grouping key
        # instead of producing separate shopping-list lines.
        name = ingredient_utils.normalize_name(raw_name) or raw_name.lower()
        try:
            qty = float(ing["quantity"]) if ing.get("quantity") is not None else None
        except (TypeError, ValueError):
            qty = None
        unit_raw = ing.get("unit")
        unit = (
            unit_raw.get("name") if isinstance(unit_raw, dict)
            else str(unit_raw) if unit_raw
            else None
        )
        ingredients.append({
            "name":     name.lower(),
            "quantity": qty,
            "unit":     _canonical_unit(unit),
        })
    return ingredients


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate(
    all_ingredients: list[dict],
) -> dict[tuple[str, Optional[str]], float]:
    """
    Combine same-named ingredients into one buy-list line (Patch 15).

    Grouping is by normalized name first. Within a name group, quantities
    in the same unit family (all-volume, e.g. tsp/tbsp/cup, or all-mass,
    e.g. g/kg/oz/lb) are converted to a common base unit, summed, and
    converted back to one sensible display unit — so "2 tbsp olive oil" +
    "1 tsp olive oil" becomes one "2.33 tbsp olive oil" line instead of
    two separate ones. Units outside those two families (each, clove,
    can, etc.) or with no unit at all are summed only when they already
    match exactly, same as before — never guessed at or force-combined
    across incompatible units (e.g. "2 cloves garlic" stays separate from
    "1 tsp garlic powder").
    """
    by_name: dict[str, list[dict]] = defaultdict(list)
    for ing in all_ingredients:
        by_name[ing["name"]].append(ing)

    totals: dict[tuple[str, Optional[str]], float] = defaultdict(float)
    for name, entries in by_name.items():
        vol_base  = 0.0
        mass_base = 0.0
        have_vol  = False
        have_mass = False
        for e in entries:
            qty  = e["quantity"] or 0.0
            unit = e["unit"]
            fam  = ingredient_utils.unit_family(unit)
            if fam == "volume":
                vol_base += ingredient_utils.to_base(qty, unit)
                have_vol = True
            elif fam == "mass":
                mass_base += ingredient_utils.to_base(qty, unit)
                have_mass = True
            else:
                totals[(name, unit)] += qty
        if have_vol:
            disp_qty, disp_unit = ingredient_utils.from_base(vol_base, "volume")
            totals[(name, disp_unit)] += disp_qty
        if have_mass:
            disp_qty, disp_unit = ingredient_utils.from_base(mass_base, "mass")
            totals[(name, disp_unit)] += disp_qty
    return dict(totals)


# ── Pantry + staples separation ───────────────────────────────────────────────

def _apply_pantry_and_staples(
    totals: dict[tuple[str, Optional[str]], float],
    pantry_items: list,
    staples: list[str],
    warnings: list[str],
) -> tuple[
    dict[tuple[str, Optional[str]], float],  # remaining (to buy)
    list[dict],                               # pantry_check (staples with qty)
    list[dict],                               # using_from_pantry
]:
    remaining       = dict(totals)
    pantry_check:   list[dict] = []   # staples needed — verify on hand
    using_from_pantry: list[dict] = []
    staples_lower   = {s.lower() for s in staples}

    # ── Staples pass ────────────────────────────────────────────────────────
    # Staples are assumed on hand. Remove from the buy list but record
    # what quantity is needed in the pantry_check section.
    for key in list(remaining.keys()):
        name, unit = key
        is_staple = any(ingredient_utils.names_match(name, s) for s in staples_lower)
        if is_staple:
            qty = remaining.pop(key)
            pantry_check.append({
                "item":     name,
                "quantity": round(qty, 2) if qty else None,
                "unit":     unit,
                "note":     "assumed on hand — verify quantity before cooking",
            })

    # ── Tracked pantry pass ──────────────────────────────────────────────────
    for pitem in pantry_items:
        pname = pitem.name.lower()
        punit = _canonical_unit(pitem.unit)
        pqty  = float(pitem.quantity) if pitem.quantity is not None else None

        matched_key = next(
            (k for k in list(remaining.keys()) if ingredient_utils.names_match(pname, k[0])),
            None,
        )
        if matched_key is None:
            continue

        need_qty, need_unit = remaining[matched_key], matched_key[1]

        if pqty is not None and need_qty and need_unit and punit == need_unit:
            used = min(pqty, need_qty)
            using_from_pantry.append({
                "item":     matched_key[0],
                "quantity": round(used, 2),
                "unit":     need_unit,
                "note":     "will deplete remaining stock" if used >= pqty else None,
            })
            leftover = need_qty - used
            if leftover <= 0:
                del remaining[matched_key]
            else:
                remaining[matched_key] = leftover
        else:
            if punit and need_unit and punit != need_unit:
                warnings.append(
                    f"Unit mismatch for '{matched_key[0]}': "
                    f"pantry has {punit}, recipe needs {need_unit}. "
                    f"Verify on hand and adjust if needed."
                )
            using_from_pantry.append({
                "item":     matched_key[0],
                "quantity": pqty,
                "unit":     punit,
                "note":     "on hand — verify quantity before shopping",
            })
            del remaining[matched_key]

    return remaining, pantry_check, using_from_pantry


# ── Package size rounding ─────────────────────────────────────────────────────

def _round_to_package(
    name: str,
    qty: float,
    unit: Optional[str],
    pkg_sizes: dict,
) -> tuple[Optional[float], Optional[str], Optional[str], Optional[int]]:
    for key, spec in pkg_sizes.items():
        if ingredient_utils.names_match(key.lower(), name):
            pkg_unit  = spec.get("unit")
            pkg_size  = float(spec.get("package_size", 1))
            pkg_label = spec.get("package_label")
            if pkg_unit == unit or not unit:
                n     = math.ceil(qty / pkg_size) if qty > 0 else 1
                return round(n * pkg_size, 2), pkg_unit, pkg_label, n
    return (round(qty, 2) if qty else None, unit, None, None)


# ── Public entry point ────────────────────────────────────────────────────────

def build_shopping_list(
    household_id: str,
    week_start: date,
    db: Session,
) -> dict:
    household = db.query(models.Household).filter(
        models.Household.id == household_id
    ).first()
    if not household:
        raise ValueError(f"Household {household_id!r} not found")

    selections = db.query(models.WeeklySelection).filter(
        models.WeeklySelection.household_id == household_id,
        models.WeeklySelection.week_start_date == week_start,
    ).all()
    if not selections:
        raise ValueError(
            f"No selections found for week {week_start}. "
            "Confirm recipe selections first via POST /meal-plan/selections."
        )

    recipe_rows  = {
        r.id: r
        for r in db.query(models.Recipe).filter(
            models.Recipe.id.in_([s.recipe_id for s in selections])
        ).all()
    }
    pkg_sizes    = config_files.get_package_sizes()
    pantry_items = db.query(models.PantryItem).filter(
        models.PantryItem.household_id == household_id
    ).all()
    staples      = config_files.get_staples()
    warnings:    list[str] = []

    # ── Step 1–2: fetch + scale ───────────────────────────────────────────
    all_ingredients: list[dict] = []
    selected_titles: list[str]  = []
    missing_mealie:  list[str]  = []

    log.info("Shopping list: %d selections for week %s", len(selections), week_start)
    for sel in selections:
        recipe = recipe_rows.get(sel.recipe_id)
        if not recipe:
            warnings.append(f"Recipe ID {sel.recipe_id} not found — skipped.")
            continue
        selected_titles.append(recipe.title)
        log.info("Processing recipe: '%s' | mealie_slug=%s", recipe.title, recipe.mealie_slug or "NONE")

        if not recipe.mealie_slug:
            # Fallback: use scraped ingredient strings stored at discovery time.
            # Patch 15: parse a best-effort quantity/unit/name out of each raw
            # line (instead of treating the whole string as an opaque name) so
            # these can combine with each other and with Mealie-sourced
            # ingredients in the same aggregation pass, rather than each
            # scraped recipe producing its own disconnected line items.
            if recipe.scraped_ingredients_json:
                import json as _json
                try:
                    ing_strings = _json.loads(recipe.scraped_ingredients_json)
                    for ing_str in ing_strings:
                        if ing_str.strip():
                            all_ingredients.append(
                                ingredient_utils.parse_scraped_ingredient(ing_str)
                            )
                    warnings.append(
                        f"'{recipe.title}': using scraped ingredient list (not yet in Mealie — "                        f"quantities are best-effort parsed, not household-scaled; confirm and check Mealie import).")
                except Exception:
                    missing_mealie.append(recipe.title)
            else:
                warnings.append(
                    f"'{recipe.title}' has no ingredient data — confirm selections "                    f"to trigger Mealie import, then regenerate shopping list.")
                missing_mealie.append(recipe.title)
            continue

        try:
            detail   = mealie_client.get_recipe(recipe.mealie_slug)
            servings = _parse_servings(detail)
            scale    = (household.num_people / servings) if servings and servings > 0 else 1.0
            ings = _extract_ingredients(detail)
            log.info("Mealie fetch OK: '%s' — %d ingredients, servings=%s, scale=%.2f",
                     recipe.title, len(ings), servings, scale)
            for ing in ings:
                scaled = round(ing["quantity"] * scale, 3) if ing["quantity"] else None
                all_ingredients.append({**ing, "quantity": scaled})
        except mealie_client.MealieError as e:
            log.warning("Mealie fetch FAILED for '%s' (slug=%s): %s", recipe.title, recipe.mealie_slug, e)
            warnings.append(f"Mealie error for '{recipe.title}': {e} — add manually.")
            missing_mealie.append(recipe.title)

    # ── Step 3: aggregate ─────────────────────────────────────────────────
    totals = _aggregate(all_ingredients)

    # ── Step 4–5: pantry + staples ────────────────────────────────────────
    remaining, pantry_check, using_from_pantry = _apply_pantry_and_staples(
        totals, pantry_items, staples, warnings
    )

    # ── Step 6–7: round + group ───────────────────────────────────────────
    shopping_by_section: dict[str, list[dict]] = defaultdict(list)

    for (name, unit), qty in sorted(remaining.items(), key=lambda x: x[0][0]):
        rounded_qty, final_unit, pkg_label, n_pkgs = _round_to_package(
            name, qty, unit, pkg_sizes
        )
        shopping_by_section[_section_for(name)].append({
            "item":            name,
            "quantity":        rounded_qty,
            "unit":            final_unit,
            "package_label":   pkg_label,
            "packages_needed": n_pkgs,
            "note":            "as needed — no quantity in recipe" if qty == 0 else None,
        })

    if missing_mealie:
        warnings.insert(0,
            f"Ingredient data unavailable for: {', '.join(missing_mealie)}. "
            "These were excluded from the quantity calculation."
        )

    log.info(
        "Shopping list complete: %d sections, %d buy items, %d pantry check, %d warnings",
        len(shopping_by_section),
        sum(len(v) for v in shopping_by_section.values()),
        len(pantry_check),
        len(warnings),
    )
    return {
        "week_start_date":        week_start,
        "household_id":           household_id,
        "selected_recipe_titles": selected_titles,
        "shopping_by_section":    dict(shopping_by_section),
        "pantry_check":           sorted(pantry_check, key=lambda x: x["item"]),
        "using_from_pantry":      using_from_pantry,
        "warnings":               warnings,
    }
