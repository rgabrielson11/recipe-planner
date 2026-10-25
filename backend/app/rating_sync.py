"""
Push star ratings from Past Meals to Mealie.

A rating is sent when it is given, but it can't be if the recipe isn't in
Mealie yet (the import runs in the background after you confirm the week) or
Mealie is unreachable.  MealPlanEntry.mealie_rating_synced_at records
delivery; sync_pending_ratings() retries anything undelivered.  It runs after
each background Mealie import and in the nightly job, and never re-sends a
delivered rating, so ratings changed directly in Mealie aren't overwritten.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app import mealie_client, models

log = logging.getLogger(__name__)


def push_entry_rating(db: Session, entry: models.MealPlanEntry) -> bool:
    """Send one entry's rating to Mealie. Returns True if delivered."""
    recipe = entry.recipe
    if entry.rating is None or not recipe or not recipe.mealie_slug or not mealie_client.is_configured():
        return False
    try:
        mealie_client.set_recipe_rating(recipe.mealie_slug, entry.rating, favorite=bool(entry.is_favorite))
    except mealie_client.MealieError as e:
        log.warning("Mealie rating sync failed for '%s' (will retry): %s", recipe.title, e)
        return False
    entry.mealie_rating_synced_at = datetime.utcnow()
    db.commit()
    log.info("Rating %d★ synced to Mealie: '%s'", entry.rating, recipe.title)
    return True


def sync_pending_ratings(db: Session) -> dict:
    """Deliver every rating Mealie hasn't received yet (latest rating per recipe)."""
    if not mealie_client.is_configured():
        return {"synced": 0, "failed": 0, "waiting_for_import": 0}
    latest: dict = {}
    for e in db.query(models.MealPlanEntry).filter(models.MealPlanEntry.rating.isnot(None)).all():
        cur = latest.get(e.recipe_id)
        if cur is None or (e.reviewed_at or datetime.min) > (cur.reviewed_at or datetime.min):
            latest[e.recipe_id] = e
    synced = failed = waiting = 0
    for e in latest.values():
        delivered = e.mealie_rating_synced_at and (not e.reviewed_at or e.mealie_rating_synced_at >= e.reviewed_at)
        if delivered:
            continue
        if not (e.recipe and e.recipe.mealie_slug):
            waiting += 1
            continue
        if push_entry_rating(db, e):
            synced += 1
        else:
            failed += 1
    if synced or failed:
        log.info("Rating sync: %d synced, %d failed, %d waiting for Mealie import", synced, failed, waiting)
    return {"synced": synced, "failed": failed, "waiting_for_import": waiting}
