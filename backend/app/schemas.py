from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ── Household ─────────────────────────────────────────────────────────────────

class HouseholdCreate(BaseModel):
    name: str = "My Household"
    num_people: int = 4


class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    num_people: int
    created_at: datetime


# ── Preferences ───────────────────────────────────────────────────────────────

class PreferenceCreate(BaseModel):
    household_id: str
    liked_items: list[str] = []
    disliked_items: list[str] = []        # soft: deprioritise, never block
    excluded_items: list[str] = []        # hard: allergies / never-make
    max_cook_time_minutes: Optional[int] = None
    skill_level: Optional[str] = None     # beginner | intermediate | advanced
    available_methods: list[str] = []
    available_cookware: list[str] = []
    recipe_options_per_meal: int = 3
    default_num_suggestions: int = 10     # flat weekly suggestion pool size
    favorite_rating_threshold: int = 4
    mealie_dinner_tag: str = "dinner-planner"
    bring_list_name: Optional[str] = None   # Patch 16
    notes: Optional[str] = None


class PreferenceUpdate(BaseModel):
    liked_items: Optional[list[str]] = None
    disliked_items: Optional[list[str]] = None
    excluded_items: Optional[list[str]] = None
    max_cook_time_minutes: Optional[int] = None
    skill_level: Optional[str] = None
    available_methods: Optional[list[str]] = None
    available_cookware: Optional[list[str]] = None
    recipe_options_per_meal: Optional[int] = None
    default_num_suggestions: Optional[int] = None
    favorite_rating_threshold: Optional[int] = None
    mealie_dinner_tag: Optional[str] = None
    bring_list_name: Optional[str] = None   # Patch 16
    notes: Optional[str] = None


class PreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    liked_items: list[str]
    disliked_items: list[str]
    excluded_items: list[str]
    max_cook_time_minutes: Optional[int]
    skill_level: Optional[str]
    available_methods: list[str]
    available_cookware: list[str]
    recipe_options_per_meal: int
    default_num_suggestions: int
    favorite_rating_threshold: int
    mealie_dinner_tag: str
    bring_list_name: Optional[str] = None   # Patch 16
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── Pantry staples ────────────────────────────────────────────────────────────

class StapleCreate(BaseModel):
    name: str


# ── Pantry items ──────────────────────────────────────────────────────────────

class PantryItemCreate(BaseModel):
    household_id: str
    name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    expiry_date: Optional[date] = None


class PantryItemUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    expiry_date: Optional[date] = None


class PantryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    name: str
    quantity: Optional[float]
    unit: Optional[str]
    category: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime
    updated_at: datetime


# ── Recipes ───────────────────────────────────────────────────────────────────

class RecipeCreate(BaseModel):
    source_url: str
    title: str


class RecipeImport(BaseModel):
    source_url: str


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_url: str
    title: str
    mealie_slug: Optional[str]
    created_at: datetime


# ── Recipe rejections ─────────────────────────────────────────────────────────

class RejectionCreate(BaseModel):
    household_id: str
    reason_category: str       # key from rejection_reasons.yaml
    reason_detail: Optional[str] = None
    rejected_week: Optional[date] = None   # defaults to current week if omitted


class RejectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    recipe_id: str
    reason_category: str
    reason_detail: Optional[str]
    is_permanent: bool
    rejected_week: Optional[date]
    suppress_weeks: Optional[int]
    created_at: datetime


# ── Weekly intent ─────────────────────────────────────────────────────────────

class WeeklyIntentCreate(BaseModel):
    """
    Recorded at the start of each planning session.

    ingredient_hints  — free-text keywords to feature this week.
                        e.g. ["chicken thighs", "salmon", "bbq", "Asian"]
                        Each hint found in a recipe adds +15 pts (max +45),
                        making it the strongest per-recipe signal this week.

    num_suggestions   — how many recipes to pull in the flat suggestion list.
                        Varies week to week; falls back to
                        default_num_suggestions from preferences when omitted.

    pantry_snapshot_notes — free-text notes from the pantry check-in.
                            Not used by the engine; kept as a human record.
    """
    ingredient_hints: list[str] = []
    num_suggestions: Optional[int] = None
    pantry_snapshot_notes: Optional[str] = None


class WeeklyIntentUpdate(BaseModel):
    ingredient_hints: Optional[list[str]] = None
    num_suggestions: Optional[int] = None
    pantry_snapshot_notes: Optional[str] = None


class WeeklyIntentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    week_start_date: date
    ingredient_hints: list[str]
    num_suggestions: Optional[int]
    pantry_snapshot_notes: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── Weekly selections ─────────────────────────────────────────────────────────

class WeeklySelectionCreate(BaseModel):
    """
    Locks in the household's chosen recipes for the week after reviewing
    the suggestion list. The shopping list generates ONLY from these.
    One call replaces any prior selections for that week.
    """
    household_id: str
    week_start_date: date
    recipe_ids: list[str]


class WeeklySelectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    week_start_date: date
    recipe_id: str
    created_at: datetime


class MealieImportResult(BaseModel):
    title: str
    status: str       # "imported" | "already_in_mealie" | "import_failed"
    slug: Optional[str] = None
    error: Optional[str] = None


class WeeklySelectionSummary(BaseModel):
    """Response to POST /meal-plan/selections — includes Mealie import results."""
    week_start_date: date
    household_id: str
    selected_recipes: list[RecipeOut]
    meal_plan_entry_ids: list[str]
    mealie_imports: list[MealieImportResult]


# ── Meal plan entries ─────────────────────────────────────────────────────────

class MealPlanEntryCreate(BaseModel):
    household_id: str
    recipe_id: str
    week_start_date: date


class MealPlanEntryReview(BaseModel):
    rating: int   # 1-5


class MealPlanEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    household_id: str
    recipe_id: str
    week_start_date: date
    rating: Optional[int]
    is_favorite: bool
    reviewed_at: Optional[datetime]
    created_at: datetime


# ── Matching engine / weekly suggestions (flat list) ──────────────────────────

class SuggestedRecipe(BaseModel):
    """One ranked recipe in the flat weekly suggestion list."""
    rank: int
    recipe_id: Optional[str]
    title: str
    mealie_slug: Optional[str] = None   # None for discovered recipes not yet in Mealie
    source_url: str
    score: float
    pantry_overlap_pct: float       # 0–100
    missing_ingredients: list[str]
    is_favorite: bool
    total_time_minutes: Optional[int]
    protein_category: Optional[str] = "other"   # classified by title/ingredient keywords
    carbs_per_serving: Optional[float] = None  # grams carbs per serving from nutrition data
    scraped_servings: Optional[str] = None    # e.g. "4 servings"


class WeeklySuggestion(BaseModel):
    """Flat ranked suggestion list returned by GET /meal-plan/suggest."""
    week_start_date: str
    household_id: str
    num_suggestions: int
    mealie_available: bool
    dinner_tag_filter: str
    weekly_hints_applied: list[str]
    mealie_favorites_shown: int     # Pool A: proven Mealie 4★+ recipes
    discoveries_shown: int          # Pool B: newly scraped from recipe sites
    suggestions: list[SuggestedRecipe]


# ── Shopping list ─────────────────────────────────────────────────────────────

class ShoppingLineItem(BaseModel):
    item: str
    quantity: Optional[float]
    unit: Optional[str]
    package_label: Optional[str]    # e.g. "1 lb pack", "dozen"
    packages_needed: Optional[int]  # how many of that package to buy
    note: Optional[str]             # e.g. "check pantry — partial bag on hand"


class PantryUseItem(BaseModel):
    item: str
    quantity: Optional[float]
    unit: Optional[str]
    note: Optional[str]             # e.g. "will deplete remaining stock"


class ShoppingList(BaseModel):
    """
    shopping_by_section  -> items to BUY, grouped by store section
    pantry_check         -> staples needed (assumed on hand; verify qty before cooking)
    using_from_pantry    -> tracked pantry items consumed this week
    """
    week_start_date: date
    household_id: str
    selected_recipe_titles: list[str]
    shopping_by_section: dict[str, list[ShoppingLineItem]]
    pantry_check: list[PantryUseItem]
    using_from_pantry: list[PantryUseItem]
    warnings: list[str]
