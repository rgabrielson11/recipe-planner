"""
Ingredient name/unit normalization for the shopping list generator.

Two problems this solves for `shopping_list.py`:

1. "Same" ingredients don't combine into one buy-list line because their
   names differ cosmetically — case, whitespace, trailing "s", or a
   comma-appended prep note ("yellow onion, diced" vs "Yellow Onion").
2. Recipes not yet imported into Mealie (still just a scraped stub) had
   their ingredients stored as opaque raw strings with no parsed
   quantity/unit, so they could never combine with anything — every
   scraped recipe produced its own disconnected line items.

This module also does light unit conversion (volume<->volume,
mass<->mass) so "2 tbsp olive oil" and "1 tsp olive oil" across two
recipes land on one combined line instead of two.

Deliberately NOT shared with recipe_discovery.py's pantry-matching regex —
that code is already deployed and tested; keeping this self-contained
avoids touching it for an unrelated feature.
"""

import re
from typing import Optional

# ── Unit aliases / canonicalisation ────────────────────────────────────────────

_UNIT_ALIASES: dict[str, str] = {
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsps": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
    "cup": "cup", "cups": "cup",
    "fluid ounce": "fl oz", "fluid ounces": "fl oz", "fl. oz": "fl oz", "fl oz": "fl oz",
    "pint": "pt", "pints": "pt", "pt": "pt",
    "quart": "qt", "quarts": "qt", "qt": "qt",
    "gallon": "gal", "gallons": "gal", "gal": "gal",
    "milliliter": "ml", "milliliters": "ml", "ml": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l", "l": "l",
    "ounce": "oz", "ounces": "oz", "oz": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "lb": "lb",
    "gram": "g", "grams": "g", "g": "g",
    "kilogram": "kg", "kilograms": "kg", "kg": "kg",
    "each": "each", "whole": "each", "piece": "each", "pieces": "each",
    "slice": "slice", "slices": "slice",
    "clove": "clove", "cloves": "clove",
    "sprig": "sprig", "sprigs": "sprig",
    "bunch": "bunch", "bunches": "bunch",
    "can": "can", "cans": "can",
    "package": "pkg", "packages": "pkg", "pkg": "pkg", "pkgs": "pkg",
    "head": "head", "heads": "head",
    "pinch": "pinch", "pinches": "pinch",
    "dash": "dash", "dashes": "dash",
}


def canonical_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    return _UNIT_ALIASES.get(unit.lower().strip(), unit.lower().strip())


# ── Unit conversion (within a family only — never crosses volume/mass) ────────

_VOL_TO_TSP: dict[str, float] = {
    "tsp": 1.0, "tbsp": 3.0, "fl oz": 6.0, "cup": 48.0,
    "pt": 96.0, "qt": 192.0, "gal": 768.0,
}
_MASS_TO_G: dict[str, float] = {
    "g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592,
}


def unit_family(unit: Optional[str]) -> Optional[str]:
    if unit in _VOL_TO_TSP:
        return "volume"
    if unit in _MASS_TO_G:
        return "mass"
    return None


def to_base(qty: float, unit: str) -> float:
    """Convert to the family's base unit (tsp for volume, g for mass)."""
    fam = unit_family(unit)
    if fam == "volume":
        return qty * _VOL_TO_TSP[unit]
    if fam == "mass":
        return qty * _MASS_TO_G[unit]
    return qty


def from_base(qty_base: float, family: str) -> tuple[float, Optional[str]]:
    """Convert a base-unit total back to the largest sensible display unit."""
    if family == "volume":
        if qty_base >= _VOL_TO_TSP["cup"]:
            return round(qty_base / _VOL_TO_TSP["cup"], 2), "cup"
        if qty_base >= _VOL_TO_TSP["tbsp"]:
            return round(qty_base / _VOL_TO_TSP["tbsp"], 2), "tbsp"
        return round(qty_base, 2), "tsp"
    if family == "mass":
        if qty_base >= _MASS_TO_G["lb"]:
            return round(qty_base / _MASS_TO_G["lb"], 2), "lb"
        if qty_base >= _MASS_TO_G["oz"]:
            return round(qty_base / _MASS_TO_G["oz"], 2), "oz"
        return round(qty_base, 1), "g"
    return round(qty_base, 2), None


# ── Name normalisation ──────────────────────────────────────────────────────────

_QTY_UNIT_PREFIX_RE = re.compile(
    r"^\s*[\d¼½¾⅓⅔⅛⅜⅝⅞]+(?:\s+\d+/\d+|/\d+|\.\d+)?\s*"
    r"(?:(?:cups?|tbsp|tablespoons?|tsp|teaspoons?|lbs?|pounds?|oz|ounces?|"
    r"fl\.?\s?oz|fluid\s+ounces?|grams?|kilograms?|kg|milliliters?|ml|"
    r"liters?|litres?|l|pints?|pt|quarts?|qt|gallons?|gal|"
    r"cloves?|heads?|bunches?|slices?|pieces?|cans?|packages?|pkgs?|"
    r"sprigs?|pinch(?:es)?|dash(?:es)?|g)\b)?\s*",
    re.IGNORECASE,
)

# Leading prep-instruction / size words that appear *before* the food name
# in free-text ingredient lines ("2 cups finely chopped yellow onion") —
# stripped repeatedly (there can be more than one, e.g. "finely chopped").
# Deliberately excludes words like "ground" or "frozen" that are part of
# the actual product identity ("ground beef", "frozen peas") rather than a
# prep instruction, so those are never merged with a differently-prepared
# version of the same base ingredient.
_LEADING_DESCRIPTOR_RE = re.compile(
    r"^(large|medium|small|whole|fresh|dried|chopped|diced|minced|sliced|"
    r"crushed|grated|shredded|peeled|trimmed|halved|quartered|cubed|"
    r"torn|crumbled|coarsely|finely|thinly|roughly)\s+",
    re.IGNORECASE,
)

# Words that end in "s" but are already the correct singular/mass-noun form
# for a food item — don't strip the trailing "s" off these.
_PLURAL_EXCEPTIONS = {
    "molasses", "hummus", "asparagus", "swiss", "citrus", "couscous",
    "chives", "greens", "grits", "oats", "noodles", "beans", "peas",
    "lentils", "breadcrumbs", "capers", "olives", "greens", "chips",
    "oreos", "cheerios",
}


def normalize_name(raw: str) -> str:
    """
    Reduce a raw ingredient name or free-text ingredient line to a
    canonical lowercase form suitable for grouping — strips a leading
    quantity/unit, leading prep-instruction/size words ("finely chopped",
    "large"), a parenthetical aside, and a trailing comma-appended prep
    note, then lightly singularises.

    Deliberately conservative: it does NOT strip words like "ground" or
    "frozen" that are part of the actual product name ("ground beef",
    "frozen peas") rather than a prep instruction, to avoid merging
    genuinely different shopping items.
    """
    if not raw:
        return ""
    text = raw.strip().lower()
    text = re.sub(r"\([^)]*\)", "", text)          # drop "(15 oz can)" asides
    text = _QTY_UNIT_PREFIX_RE.sub("", text, count=1).strip()
    while True:
        stripped = _LEADING_DESCRIPTOR_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped.strip()
    text = text.split(",")[0].strip()               # drop ", diced" etc.
    text = re.sub(r"\s+", " ", text).strip()
    if (
        len(text) > 3
        and text.endswith("s")
        and not text.endswith("ss")
        and not text.endswith("us")
        and text not in _PLURAL_EXCEPTIONS
    ):
        text = text[:-1]
    return text


# ── Free-text ingredient parsing (for scraped, not-yet-imported recipes) ──────

_QTY_TOKEN_RE = re.compile(
    r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+\.\d+|\d+|[¼½¾⅓⅔⅛⅜⅝⅞])\s*"
)
_UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
_UNIT_TOKEN_RE = re.compile(
    r"^(cups?|tbsp|tablespoons?|tsp|teaspoons?|lbs?|pounds?|oz|ounces?|"
    r"fl\.?\s?oz|fluid\s+ounces?|g|grams?|kg|kilograms?|ml|milliliters?|"
    r"l|liters?|litres?|pt|pints?|qt|quarts?|gal|gallons?|"
    r"cloves?|heads?|bunches?|slices?|pieces?|cans?|packages?|pkgs?|"
    r"sprigs?|pinch(?:es)?|dash(?:es)?)\b\s*",
    re.IGNORECASE,
)


def _parse_qty_token(tok: str) -> Optional[float]:
    tok = tok.strip()
    try:
        if tok in _UNICODE_FRACTIONS:
            return _UNICODE_FRACTIONS[tok]
        if " " in tok:  # mixed number, e.g. "1 1/2"
            whole, frac = tok.split(" ", 1)
            n, d = frac.split("/")
            return float(whole) + float(n) / float(d)
        if "/" in tok:
            n, d = tok.split("/")
            return float(n) / float(d)
        return float(tok)
    except (ValueError, ZeroDivisionError):
        return None


# ── "Same item?" matching (staples, tracked pantry, package sizes) ────────────

# Suffix words that turn a raw ingredient into a materially different
# processed product — "garlic" and "garlic powder" are not the same
# shopping-list item, even though one name contains the other.
_COMPOUND_MODIFIERS = {
    "powder", "extract", "paste", "sauce", "broth", "stock",
    "seasoning", "flakes", "juice", "spray",
}


def names_match(a: str, b: str) -> bool:
    """
    True if `a` and `b` refer to the same shopping-list item. Handles
    adjective-before-noun phrasing ("kosher salt" ~ "salt", "extra virgin
    olive oil" ~ "olive oil") without conflating a raw ingredient with a
    differently-processed form of it ("garlic" is NOT "garlic powder";
    "onion" is NOT "onion powder") — found while testing Patch 15, where
    the old plain substring check silently pulled "garlic" out of the buy
    list because the staples list contains "garlic powder".
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b + " "):
        return a[len(b):].strip() not in _COMPOUND_MODIFIERS
    if b.startswith(a + " "):
        return b[len(a):].strip() not in _COMPOUND_MODIFIERS
    return a in b or b in a


def parse_scraped_ingredient(raw: str) -> dict:
    """
    Best-effort split of a free-text scraped ingredient line (e.g.
    "2 1/2 cups chopped yellow onion") into {name, quantity, unit}, so a
    recipe that hasn't been imported into Mealie yet still contributes to
    the same combined totals as Mealie-sourced ingredients rather than
    sitting off to the side as an unparsed string.
    """
    text = raw.strip()
    quantity: Optional[float] = None
    unit: Optional[str] = None

    m = _QTY_TOKEN_RE.match(text)
    if m:
        quantity = _parse_qty_token(m.group(1))
        text = text[m.end():]

    mu = _UNIT_TOKEN_RE.match(text)
    if mu:
        unit = canonical_unit(mu.group(1))
        text = text[mu.end():]

    name = normalize_name(text) or normalize_name(raw)
    return {"name": name, "quantity": quantity, "unit": unit}
