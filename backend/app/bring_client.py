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
from app import ollama_client as _oc

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


def _build_catalog_lookup(translations: dict) -> dict[str, str]:
    """
    Build reverse lookup {name_lower: article_id} from Bring!'s catalog.
    Translations: {locale: {article_id: translated_name}}.
    We use article_id as itemId in save_item() — Bring! maps this to the
    catalog article with its grocery section (Meat, Produce, Dairy, etc.).
    """
    lookup: dict[str, str] = {}
    for locale_dict in translations.values():
        if not isinstance(locale_dict, dict):
            continue
        for article_id, catalog_name in locale_dict.items():
            if isinstance(catalog_name, str) and catalog_name.strip():
                lookup[catalog_name.lower()] = article_id
    return lookup


def _match_catalog(name: str, lookup: dict[str, str]) -> Optional[str]:
    """
    Return Bring! article_id for `name`, or None. Sends article_id as
    itemId so Bring! categorises the item into the right grocery section.
    """
    nl = name.lower().strip()
    if nl in lookup:
        return lookup[nl]
    best_id: Optional[str] = None
    best_len = 0
    for catalog_key, article_id in lookup.items():
        if len(catalog_key) > 2 and catalog_key in nl and len(catalog_key) > best_len:
            best_id, best_len = article_id, len(catalog_key)
    if best_id:
        return best_id
    matches = [(ck, aid) for ck, aid in lookup.items() if nl in ck and len(nl) > 2]
    if matches:
        return min(matches, key=lambda x: len(x[0]))[1]
    return None



async def push_shopping_list(shopping_list: dict, list_name: Optional[str] = None, use_ollama: bool = True) -> dict:
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
            resp = await bring.load_lists()
            lists = resp.lists  # BringListResponse dataclass in v1.x
        except BringException as e:
            raise BringError(f"Couldn't load Bring! lists: {e}") from e

        if not lists:
            raise BringError(
                "This Bring! account has no shopping lists yet — create one in the Bring! app first."
            )

        target = None
        if list_name:
            def _lst_name(l): return l.name if hasattr(l,'name') else l.get("name","")
            def _lst_uuid(l): return l.listUuid if hasattr(l,'listUuid') else l.get("listUuid","")
            target = next(
                ({"name":_lst_name(l),"listUuid":_lst_uuid(l)} for l in lists
                 if _lst_name(l).strip().lower() == list_name.strip().lower()),
                None,
            )
            if not target:
                available = ", ".join(_lst_name(l) for l in lists)
                raise BringError(
                    f"No Bring! list named '{list_name}' — available: {available}. "
                    "Update 'Bring! list name' in Settings → Preferences to match one exactly."
                )
        elif len(lists) == 1:
            l = lists[0]
            target = {"name": l.name if hasattr(l,'name') else l.get("name",""),
                      "listUuid": l.listUuid if hasattr(l,'listUuid') else l.get("listUuid","")}
        else:
            def _lst_name(l): return l.name if hasattr(l,'name') else l.get("name","")
            available = ", ".join(_lst_name(l) for l in lists)
            raise BringError(
                f"Your Bring! account has {len(lists)} lists ({available}) — set which one to "
                "use in Settings → Preferences → 'Bring! list name'."
            )

        # Load catalog translations so ingredient names match Bring!'s item database.
        # Must call reload_user_list_settings() first so reload_article_translations()
        # knows which locale the list uses. Also explicitly set list article language
        # to en-US on the target list to ensure the English catalog is loaded.
        catalog_lookup: dict[str, str] = {}
        try:
            await bring.reload_user_list_settings()
            await bring.reload_article_translations()
            raw_translations = getattr(bring, "_Bring__translations", {})
            catalog_lookup = _build_catalog_lookup(raw_translations)

            # If translations are empty (e.g. list is set to de-CH default locale
            # which the library skips), directly inject en-US translations via HTTP
            if not catalog_lookup:
                log.info("Bring! catalog empty after reload — fetching en-US directly")
                try:
                    import aiohttp as _aio
                    async with _aio.ClientSession() as _s:
                        async with _s.get(
                            "https://web.getbring.com/locale/articles.en-US.json",
                            headers=bring.headers,
                        ) as _r:
                            if _r.status == 200:
                                en_dict = await _r.json(content_type=None)
                                catalog_lookup = _build_catalog_lookup({"en-US": en_dict})
                                log.info("Bring! en-US catalog fetched directly: %d entries",
                                         len(catalog_lookup))
                            else:
                                log.warning("Bring! en-US fetch failed: HTTP %s", _r.status)
                except Exception as _e:
                    log.warning("Bring! en-US direct fetch failed: %s", _e)

            log.info("Bring! catalog ready: %d entries", len(catalog_lookup))
            if catalog_lookup:
                sample = list(catalog_lookup.items())[:6]
                log.info("Bring! catalog sample (name→article_id): %s", sample)
            else:
                log.warning("Bring! catalog still empty — all items will be own items")
        except Exception as e:
            log.warning("Could not load Bring! catalog — items may be uncategorized: %s", e)

        pushed: list[str] = []
        # Pre-normalize ingredient names via Ollama to improve catalog match rate.
        # Maps specialty/foreign names to common generic equivalents before
        # catalog lookup: "herbes de provence" → "mixed herbs", "arborio" → "rice"
        all_names = [item["item"] for item in items]
        if use_ollama:
            bring_names = _oc.normalize_for_bring(all_names)
            log.info("Bring! Ollama pre-normalisation: %d items", len(bring_names))
        else:
            bring_names = {n: n for n in all_names}
            log.info("Bring! Ollama pre-normalisation disabled")

        errors:  list[str] = []
        for item in items:
            raw_name = item["item"]
            # Use Ollama-normalized name for catalog matching, then try raw name as fallback
            normalized_name = bring_names.get(raw_name, raw_name)
            article_id = _match_catalog(normalized_name, catalog_lookup) if catalog_lookup else None
            if article_id is None and normalized_name != raw_name:
                article_id = _match_catalog(raw_name, catalog_lookup)
            send_name = article_id if article_id else raw_name
            if article_id:
                log.info("Bring! matched: '%s' → article_id='%s'", raw_name, article_id)
            else:
                log.info("Bring! NO MATCH for '%s' — will be own item", raw_name)
            spec = _format_spec(item)
            try:
                await bring.save_item(target["listUuid"], send_name, spec)
                pushed.append(raw_name)
            except BringException as e:
                log.warning("Bring! push failed for '%s': %s", raw_name, e)
                errors.append(f"{raw_name}: {e}")

        log.info(
            "Bring! push complete: list=%r pushed=%d errors=%d",
            target["name"], len(pushed), len(errors),
        )
        return {"list_name": target["name"], "pushed": pushed, "errors": errors}
