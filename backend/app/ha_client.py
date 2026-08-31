"""
Home Assistant shopping list client.

Pushes grocery items to a HA todo list entity via the REST API.
HA URL and token are reused from the existing MEALIE_BASE_URL/TOKEN env
pattern — HA uses HA_BASE_URL + HA_API_TOKEN.
"""
import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

HA_BASE_URL = os.getenv("HA_BASE_URL", "").rstrip("/")
HA_API_TOKEN = os.getenv("HA_API_TOKEN", "")
_TIMEOUT = 10


class HAError(Exception):
    pass


def is_configured() -> bool:
    return bool(HA_BASE_URL and HA_API_TOKEN)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HA_API_TOKEN}",
        "Content-Type": "application/json",
    }


def get_todo_lists() -> list[dict]:
    """
    Return all todo list entities available in Home Assistant.
    Each entry: {entity_id, friendly_name}
    """
    if not is_configured():
        raise HAError("HA_BASE_URL / HA_API_TOKEN not configured")
    try:
        r = requests.get(
            f"{HA_BASE_URL}/api/states",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        states = r.json()
        return [
            {
                "entity_id": s["entity_id"],
                "friendly_name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
            }
            for s in states
            if s.get("entity_id", "").startswith("todo.")
        ]
    except Exception as e:
        raise HAError(f"Failed to fetch HA todo lists: {e}") from e


def clear_list(entity_id: str) -> None:
    """Remove all incomplete items from the todo list before adding new ones."""
    try:
        r = requests.post(
            f"{HA_BASE_URL}/api/services/todo/remove_completed_items",
            headers=_headers(),
            json={"entity_id": entity_id},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:
        log.debug("HA clear list failed (non-fatal): %s", e)


def add_item(entity_id: str, name: str, description: str = "") -> None:
    """Add a single item to the HA todo list."""
    payload: dict = {"entity_id": entity_id, "item": name}
    if description:
        payload["description"] = description
    r = requests.post(
        f"{HA_BASE_URL}/api/services/todo/add_item",
        headers=_headers(),
        json=payload,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()


def push_shopping_list(shopping_list: dict, entity_id: str, clear_first: bool = True) -> dict:
    """
    Push all buy items from the shopping list to the specified HA todo list entity.
    Returns {entity_id, pushed, errors}.
    """
    if not is_configured():
        raise HAError("HA_BASE_URL / HA_API_TOKEN not configured")

    if clear_first:
        clear_list(entity_id)

    items = []
    for section_items in shopping_list.get("shopping_by_section", {}).values():
        items.extend(section_items)

    pushed = []
    errors = []
    for item in items:
        name = item.get("item", "").strip()
        if not name:
            continue
        # Build a description with quantity/unit
        qty = item.get("quantity")
        unit = item.get("unit") or ""
        pkg = item.get("package_label") or ""
        desc_parts = []
        if qty is not None:
            desc_parts.append(f"{qty} {unit}".strip())
        if pkg:
            desc_parts.append(pkg)
        description = " · ".join(desc_parts)

        try:
            add_item(entity_id, name.title(), description)
            pushed.append(name)
        except Exception as e:
            log.warning("HA push failed for '%s': %s", name, e)
            errors.append(f"{name}: {e}")

    log.info("HA push complete: entity=%s pushed=%d errors=%d", entity_id, len(pushed), len(errors))
    return {"entity_id": entity_id, "pushed": len(pushed), "errors": errors}
