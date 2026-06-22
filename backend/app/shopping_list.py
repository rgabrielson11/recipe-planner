"""
Shopping List Generator — Phase 5

Builds a pantry-first, waste-minimizing shopping list from ONLY the recipes
a household has selected for the current week (WeeklySelection rows).

Pipeline:
  1. Load selected recipes and fetch full ingredient details from Mealie
  2. Scale all quantities to household size (recipe yield → num_people)
  3. Aggregate totals across all selected recipes per ingredient+unit
  4. Subtract on-hand pantry quantities (quantity-aware when units match;
     flag as "on hand" when units differ or pantry has no quantity)
  5. Subtract staples (always assumed on hand, never bought)
  6. Round remaining quantities UP to real package sizes (package_sizes.yaml)
  7. Group by store section (produce, meat, dairy, pantry, other)

Design principles:
  • Aggregate BEFORE rounding — sum all recipes first, then one rounding pass,
    not per-recipe rounding that over-buys for each recipe individually.
  • Pantry-first — always deplete what's on hand before buying.
  • Flag unit mismatches as warnings rather than silently ignoring them.
  • Degrade gracefully if Mealie is down — produce a partial list with warnings.

Store section mapping lives in SECTION_KEYWORDS below; extend as needed
without a code change (or move to YAML if you want it hand-editable).
"""

import math
import re
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app import models, mealie_client, config_files

# ── Section keywords ──────────────────────────────────────────────────────────
# Maps ingredient name substrings → store section. First match wins.
SECTION_KEYWORDS: dict[str, list[str]] = {
    "produce": [
        "lettuce", "spinach", "kale", "arugula", "cabbage", "bok choy",
        "broccoli", "cauliflower", "carrot", "celery", "cucumber", "zucchini",
        "squash", "eggplant", "pepper", "bell pepper", "jalapeño", "chili",
        "tomato", "onion", "shallot", "scallion", "green onion", "leek",
        "garlic", "ginger", "potato", "sweet potato", "yam", "corn",
        "mushroom", "asparagus", "green bean", "pea", "edamame", "avocado",
        "lemon", "lime", "orange", "apple", "berry", "herb", "cilantro",
        "parsley", "basil", "thyme", "rosemary", "mint", "dill",
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
        "coconut milk", "broth", "stock", "soup",
        "olive", "pickle", "capers", "artichoke", "roasted pepper",
    ],
    "dry goods & pasta": [
        "pasta", "spaghetti", "penne", "fettuccine", "linguine", "rigatoni",
        "rice", "quinoa", "couscous", "orzo", "farro", "barley",
        "flour", "cornmeal", "oat", "breadcrumb", "panko",
        "lentil", "split pea", "dried bean",
    ],
    "oils, sauces & condiments": [
        "oil", "vinegar", "soy sauce", "fish sauce", "oyster sauce",
        "hot sauce", "sriracha", "worcestershire", "mustard", "ketchup",
        "mayo", "mayonnaise", "tahini", "miso", "hoisin",
    ],
    "spices & baking": [
        "salt", "pepper", "cumin", "paprika", "turmeric", "coriander",
        "cinnamon", "oregano", "thyme", "bay leaf", "chili powder",
        "garlic powder", "onion powder", "cayenne", "nutmeg", "clove",
        "sugar", "brown sugar", "honey", "maple syrup", "vanilla",
        "baking soda", "baking powder", "cornstarch", "yeast",
    ],
    "frozen": [
        "frozen", "ice cream", "frozen pea", "frozen corn",
    ],
    "beverages": [
        "wine", "beer", "broth", "juice", "stock",
    ],
}


def _section_for(ingredient_name: str) -> str:
    name_lower = ingredient_name.lower()
    for section, keywords in SECTION_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return section
    return "other"


# ── Unit normalisation ────────────────────────────────────────────────────────
# Maps common recipe unit spellings to a canonical form for aggregation.
# Only aggregates quantities when canonical units match.

_UNIT_ALIASES: dict[str, str] = {
    # volume
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsps": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
    "cup": "cup", "cups": "cup",
    "fluid ounce": "fl oz", "fluid ounces": "fl oz", "fl. oz": "fl oz",
    "pint": "pt", "pints": "pt",
    "quart": "qt", "quarts": "qt",
    "gallon": "gal", "gallons": "gal",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "ml": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    # weight
    "ounce": "oz", "ounces": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "gram": "g", "grams": "g",
    "kilogram": "kg", "kilograms": "kg",
    # count
    "each": "each", "whole": "each", "piece": "each", "pieces": "each",
    "slice": "slice", "slices": "slice",
    "clove": "clove", "cloves": "clove",
    "sprig": "sprig", "sprigs": "sprig",
    "bunch": "bunch", "bunches": "bunch",
    "can": "can", "cans": "can",
    "package": "pkg", "packages": "pkg", "pkg": "pkg",
}


def _canonical_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    return _UNIT_ALIASES.get(unit.lower().strip(), unit.lower().strip())


# ── Ingredient extraction from Mealie detail ──────────────────────────────────

def _parse_servings(detail: dict) -> Optional[float]:
    """Extracts numeric servings from a Mealie recipe detail dict."""
    # Mealie may use recipeYield (string) or recipeServings (int/float)
    raw = detail.get("recipeServings") or detail.get("recipeYield")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    # Try to extract first number from strings like "4 servings" or "Serves 6"
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw))
    return float(m.group(1)) if m else None


def _extract_ingredients(detail: dict) -> list[dict]:
    """
    Returns a list of dicts:
      { "name": str, "quantity": float|None, "unit": str|None, "note": str|None }
    """
    ingredients = []
    for ing in detail.get("recipeIngredient", []):
        if not isinstance(ing, dict):
            continue
        food = ing.get("food")
        name = (
            food.get("name", "") if isinstance(food, dict)
            else str(food) if food
            else ing.get("note", "")
        )
        name = name.strip()
        if not name:
            continue
        qty_raw = ing.get("quantity")
        try:
            qty = float(qty_raw) if qty_raw is not None else None
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
            "note":     ing.get("note", ""),
        })
    return ingredients


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate_ingredients(
    all_ingredients: list[dict],
) -> dict[tuple[str, Optional[str]], float]:
    """
    Groups ingredients by (name, canonical_unit) and sums quantities.
    Ingredients with no quantity are collected separately under quantity=0
    (they'll appear on the list as "as needed").
    """
    totals: dict[tuple[str, Optional[str]], float] = defaultdict(float)
    for ing in all_ingredients:
        key = (ing["name"], ing["unit"])
        totals[key] += ing["quantity"] or 0.0
    return dict(totals)


# ── Pantry subtraction ────────────────────────────────────────────────────────

def _subtract_pantry(
    totals: dict[tuple[str, Optional[str]], float],
    pantry_items: list[models.PantryItem],
    staples: list[str],
    warnings: list[str],
) -> tuple[
    dict[tuple[str, Optional[str]], float],
    list[dict],    # using_from_pantry
    list[str],     # staples_relied_on
]:
    """
    Subtracts pantry and staples from the aggregated ingredient totals.
    Returns (remaining, using_from_pantry, staples_relied_on).
    """
    remaining        = dict(totals)
    using_from_pantry: list[dict] = []
    staples_relied_on: list[str]  = []
    staples_lower    = {s.lower() for s in staples}

    # Staples pass — remove entirely
    for key in list(remaining.keys()):
        name, unit = key
        if any(s in name or name in s for s in staples_lower):
            staples_relied_on.append(name)
            del remaining[key]

    # Pantry pass — subtract quantities where units match
    for pitem in pantry_items:
        pname = pitem.name.lower()
        punit = _canonical_unit(pitem.unit)
        pqty  = float(pitem.quantity) if pitem.quantity is not None else None

        matched_key: Optional[tuple] = None
        for key in list(remaining.keys()):
            name, unit = key
            if pname in name or name in pname:
                matched_key = key
                break

        if matched_key is None:
            continue  # pantry item not needed this week

        need_qty  = remaining[matched_key]
        need_unit = matched_key[1]

        if pqty is not None and need_qty and need_unit and punit == need_unit:
            used    = min(pqty, need_qty)
            leftover = need_qty - used
            using_from_pantry.append({
                "item":     matched_key[0],
                "quantity": round(used, 2),
                "unit":     need_unit,
                "note":     "will deplete remaining stock" if used >= pqty else None,
            })
            if leftover <= 0:
                del remaining[matched_key]
            else:
                remaining[matched_key] = leftover
        else:
            # Unit mismatch or no quantity tracked — flag as on-hand and remove
            if punit and need_unit and punit != need_unit:
                warnings.append(
                    f"Unit mismatch for '{matched_key[0]}': "
                    f"pantry has {punit}, recipe needs {need_unit}. "
                    f"Verify on hand and adjust list if needed."
                )
            using_from_pantry.append({
                "item":     matched_key[0],
                "quantity": pqty,
                "unit":     punit,
                "note":     "on hand — verify quantity before shopping",
            })
            del remaining[matched_key]

    return remaining, using_from_pantry, staples_relied_on


# ── Package size rounding ─────────────────────────────────────────────────────

def _round_to_package(
    name: str,
    quantity: float,
    unit: Optional[str],
    pkg_sizes: dict,
) -> tuple[Optional[float], Optional[str], Optional[str], Optional[int]]:
    """
    Returns (rounded_qty, unit, package_label, packages_needed).
    Falls back to (quantity, unit, None, None) when no package size is defined.
    """
    for key, spec in pkg_sizes.items():
        if key.lower() in name or name in key.lower():
            pkg_unit  = spec.get("unit")
            pkg_size  = float(spec.get("package_size", 1))
            pkg_label = spec.get("package_label")
            if pkg_unit == unit or not unit:
                n_pkgs  = math.ceil(quantity / pkg_size) if quantity > 0 else 1
                rounded = round(n_pkgs * pkg_size, 2)
                return rounded, pkg_unit, pkg_label, n_pkgs
    return (round(quantity, 2) if quantity else None, unit, None, None)


# ── Public entry point ────────────────────────────────────────────────────────

def build_shopping_list(
    household_id: str,
    week_start: date,
    db: Session,
) -> dict:
    """
    Builds the shopping list for the given week from the household's
    confirmed WeeklySelections only.
    """
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

    # Fetch local recipe rows for all selections
    recipe_ids   = [s.recipe_id for s in selections]
    recipe_rows  = {
        r.id: r
        for r in db.query(models.Recipe).filter(models.Recipe.id.in_(recipe_ids)).all()
    }
    num_people   = household.num_people
    pkg_sizes    = config_files.get_package_sizes()
    pantry_items = db.query(models.PantryItem).filter(
        models.PantryItem.household_id == household_id
    ).all()
    staples      = config_files.get_staples()
    warnings: list[str] = []

    # ── Step 1–2: fetch + scale ───────────────────────────────────────────
    all_ingredients: list[dict] = []
    selected_titles: list[str]  = []
    missing_mealie: list[str]   = []

    for sel in selections:
        recipe = recipe_rows.get(sel.recipe_id)
        if not recipe:
            warnings.append(f"Recipe ID {sel.recipe_id} not found locally — skipped.")
            continue

        selected_titles.append(recipe.title)

        if not recipe.mealie_slug:
            warnings.append(
                f"'{recipe.title}' has no Mealie slug — ingredient details unavailable. "
                "Add to shopping list manually."
            )
            missing_mealie.append(recipe.title)
            continue

        try:
            detail   = mealie_client.get_recipe(recipe.mealie_slug)
            servings = _parse_servings(detail)
            scale    = (num_people / servings) if servings and servings > 0 else 1.0
            if abs(scale - 1.0) > 0.05 and servings:
                # Only note scaling when it's meaningful
                pass

            for ing in _extract_ingredients(detail):
                scaled_qty = round(ing["quantity"] * scale, 3) if ing["quantity"] else None
                all_ingredients.append({**ing, "quantity": scaled_qty})

        except mealie_client.MealieError as e:
            warnings.append(
                f"Could not fetch ingredients for '{recipe.title}' from Mealie: {e}. "
                "Add to shopping list manually."
            )
            missing_mealie.append(recipe.title)

    # ── Step 3: aggregate ─────────────────────────────────────────────────
    totals = _aggregate_ingredients(all_ingredients)

    # ── Step 4–5: subtract pantry + staples ───────────────────────────────
    remaining, using_from_pantry, staples_relied_on = _subtract_pantry(
        totals, pantry_items, staples, warnings
    )

    # ── Step 6–7: round to packages + group by section ────────────────────
    shopping_by_section: dict[str, list[dict]] = defaultdict(list)

    for (name, unit), qty in sorted(remaining.items(), key=lambda x: x[0][0]):
        rounded_qty, final_unit, pkg_label, n_pkgs = _round_to_package(
            name, qty, unit, pkg_sizes
        )
        section = _section_for(name)
        item_dict: dict = {
            "item":           name,
            "quantity":       rounded_qty,
            "unit":           final_unit,
            "package_label":  pkg_label,
            "packages_needed": n_pkgs,
            "note":           None,
        }
        if qty == 0:
            item_dict["note"] = "as needed — recipe lists ingredient but no quantity"
        shopping_by_section[section].append(item_dict)

    if missing_mealie:
        warnings.insert(
            0,
            f"Ingredient data missing for: {', '.join(missing_mealie)}. "
            "These recipes were excluded from the ingredient calculation."
        )

    return {
        "week_start_date":       week_start,
        "household_id":          household_id,
        "selected_recipe_titles": selected_titles,
        "shopping_by_section":   dict(shopping_by_section),
        "using_from_pantry":     using_from_pantry,
        "staples_relied_on":     sorted(set(staples_relied_on)),
        "warnings":              warnings,
    }
