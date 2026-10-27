"""
Import Recipe page: read a recipe from any link (including pages Mealie's own
importer can't read), let the user review/edit it, then create it in Mealie
and add it to the planner's recipe catalog.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models, recipe_importer as ri
from app.database import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/import", tags=["import"])


class PreviewIn(BaseModel):
    url: str
    use_ai: bool = True


class GroupIn(BaseModel):
    title: str = ""
    items: list[str]


class SaveIn(BaseModel):
    source_url: str
    name: str
    description: str = ""
    image: Optional[str] = None
    servings: str = ""
    prep_minutes: Optional[int] = None
    cook_minutes: Optional[int] = None
    total_minutes: Optional[int] = None
    ingredient_groups: list[GroupIn]
    instructions: list[str]


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Paste a full link starting with http:// or https://")
    return url


@router.post("/preview")
def preview(payload: PreviewIn):
    """Read the recipe from a link. Nothing is saved."""
    url = _check_url(payload.url)
    try:
        html, final_url = ri.fetch_html(url)
        r = ri.parse_html(html, final_url, use_llm=payload.use_ai)
    except ri.RecipeImportError as e:
        raise HTTPException(status_code=422, detail=str(e))
    r["servings"] = r.pop("yield", "") or ""
    return r


@router.post("/save")
def save(payload: SaveIn, db: Session = Depends(get_db)):
    """Create the (reviewed) recipe in Mealie and add it to the planner catalog."""
    url = _check_url(payload.source_url)
    groups = [{"title": g.title.strip(), "items": [i.strip() for i in g.items if i.strip()]}
              for g in payload.ingredient_groups]
    groups = [g for g in groups if g["items"]]
    steps = [s.strip() for s in payload.instructions if s.strip()]
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="The recipe needs a name")
    if not groups or not steps:
        raise HTTPException(status_code=422, detail="Add at least one ingredient and one step")

    r = {
        "source_url": url, "name": payload.name.strip(), "description": payload.description,
        "image": payload.image, "yield": payload.servings.strip(),
        "prep_minutes": payload.prep_minutes, "cook_minutes": payload.cook_minutes,
        "total_minutes": payload.total_minutes or ((payload.prep_minutes or 0) + (payload.cook_minutes or 0)) or None,
        "ingredient_groups": groups, "instructions": steps,
    }
    try:
        slug, existed = ri.save_to_mealie(r)
    except ri.RecipeImportError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Mealie error: {e}")

    # Planner catalog: reuse a row with this link or slug, else create one
    row = (db.query(models.Recipe).filter(models.Recipe.mealie_slug == slug).first()
           or db.query(models.Recipe).filter(models.Recipe.source_url == url).first())
    if row is None:
        row = models.Recipe(source_url=url, title=r["name"], mealie_slug=slug)
        db.add(row)
    elif not row.mealie_slug:
        row.mealie_slug = slug
    from app.recipe_discovery import _update_row_from_detail
    _update_row_from_detail(row, ri.to_planner_detail(r))
    db.commit()
    db.refresh(row)

    base = os.getenv("MEALIE_BASE_URL", "").rstrip("/")
    log.info("Import saved: '%s' → Mealie %s (existed=%s), recipe_id=%s", r["name"], slug, existed, row.id)
    return {"slug": slug, "already_existed": existed, "recipe_id": row.id,
            "mealie_url": f"{base}/g/home/r/{slug}" if base else None}
