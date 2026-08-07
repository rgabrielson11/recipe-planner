"""
Thin Bring! shopping-list wrapper (Patch 16).

Pushes the BUY items from a generated shopping list (see
shopping_list.build_shopping_list()) to a Bring! list, so they show up
natively in the Bring! app on your phone — a purpose-built, multi-user,
checkable grocery list with store-aisle icons — instead of a flat
Reminders list or a print-out.

Uses the `bring-api` PyPI package (unofficial, reverse-engineered — same
library Home Assistant's own Bring! integration is built on). Verified
against the actual installed package (0.5.7) rather than assumed from
docs, since the published PyPI release lags the GitHub README:
  - `Bring` and the exception classes are NOT re-exported from the
    top-level `bring_api` package in 0.5.7 (its __init__.py is empty) —
    they must be imported from the submodules directly.
  - `save_item()`'s optional `item_uuid` defaults to `""`, not `None`.

Credentials are read from BRING_EMAIL / BRING_PASSWORD env vars — same
pattern as MEALIE_BASE_URL / MEALIE_API_TOKEN in mealie_client.py.
"""

import logging
import os
from typing import Optional

import aiohttp
from bring_api.bring import Bring
from bring_api.exceptions import BringException

log = logging.getLogger(__name__)

BRING_EMAIL    = os.getenv("BRING_EMAIL", "")
BRING_PASSWORD = os.getenv("BRING_PASSWORD", "")
_TIMEOUT = aiohttp.ClientTimeout(total=30)


class BringError(Exception):
    """Raised on any Bring! push failure — missing config, auth, or network."""


def _check_configured() -> None:
    if not BRING_EMAIL or not BRING_PASSWORD:
        raise BringError(
            "BRING_EMAIL / BRING_PASSWORD not configured — set both in .env and restart"
        )


def _format_spec(item: dict) -> str:
    """
    Build the Bring! item's 'specification' (the small subtitle shown
    under the item name in the app) from a shopping-list line item.
    Prefers the rounded package quantity ("2 x 12 oz can") since that's
    literally what to pick up off the shelf; falls back to the raw
    combined quantity ("2.33 tbsp") when no package size is configured.
    """
    if item.get("packages_needed") and item.get("package_label"):
        return f"{item['packages_needed']} x {item['package_label']}"
    qty  = item.get("quantity")
    unit = item.get("unit")
    if qty is not None:
        return f"{qty} {unit}".strip() if unit else str(qty)
    return ""


async def list_bring_lists() -> list[dict]:
    """Return [{listUuid, name}, ...] for the configured Bring! account — used
    by the Settings UI to show which list names are available to pick from."""
    _check_configured()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        bring = Bring(session, BRING_EMAIL, BRING_PASSWORD)
        try:
            await bring.login()
            resp = await bring.load_lists()
        except BringException as e:
            raise BringError(f"Bring! login/list-fetch failed: {e}") from e
        return [{"listUuid": l["listUuid"], "name": l["name"]} for l in resp["lists"]]


async def push_shopping_list(shopping_list: dict, list_name: Optional[str] = None) -> dict:
    """
    Push every BUY item (shopping_by_section) in a built shopping-list dict
    to a Bring! list. pantry_check / using_from_pantry are deliberately
    excluded — those are assumed already on hand, not things to buy.

    save_item() adds-or-updates by item name (no uuid passed), so pushing
    the same weekly list twice updates quantities in place rather than
    creating duplicate rows on the Bring! list.

    Returns {"list_name": str, "pushed": [item names], "errors": [...]}.
    Raises BringError if not configured, login fails, or list_name (or the
    lack of one, with 2+ lists on the account) can't be resolved to a
    single list.
    """
    _check_configured()

    items = [
        item
        for section_items in shopping_list.get("shopping_by_section", {}).values()
        for item in section_items
    ]
    if not items:
        return {"list_name": None, "pushed": [], "errors": [],
                "message": "Nothing to buy this week — nothing was pushed."}

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        bring = Bring(session, BRING_EMAIL, BRING_PASSWORD)
        try:
            await bring.login()
        except BringException as e:
            raise BringError(f"Bring! login failed — check BRING_EMAIL/BRING_PASSWORD: {e}") from e

        try:
            lists = (await bring.load_lists())["lists"]
        except BringException as e:
            raise BringError(f"Couldn't load Bring! lists: {e}") from e

        if not lists:
            raise BringError(
                "This Bring! account has no shopping lists yet — create one in the Bring! app first."
            )

        target = None
        if list_name:
            target = next(
                (l for l in lists if l["name"].strip().lower() == list_name.strip().lower()),
                None,
            )
            if not target:
                available = ", ".join(l["name"] for l in lists)
                raise BringError(
                    f"No Bring! list named '{list_name}' — available: {available}. "
                    "Update 'Bring! list name' in Settings → Preferences to match one exactly."
                )
        elif len(lists) == 1:
            target = lists[0]
        else:
            available = ", ".join(l["name"] for l in lists)
            raise BringError(
                f"Your Bring! account has {len(lists)} lists ({available}) — set which one to "
                "use in Settings → Preferences → 'Bring! list name'."
            )

        pushed: list[str] = []
        errors:  list[str] = []
        for item in items:
            name = item["item"]
            spec = _format_spec(item)
            try:
                await bring.save_item(target["listUuid"], name, spec)
                pushed.append(name)
            except BringException as e:
                log.warning("Bring! push failed for '%s': %s", name, e)
                errors.append(f"{name}: {e}")

        log.info(
            "Bring! push complete: list=%r pushed=%d errors=%d",
            target["name"], len(pushed), len(errors),
        )
        return {"list_name": target["name"], "pushed": pushed, "errors": errors}
