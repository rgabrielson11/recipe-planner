"""
Ollama client for local LLM inference.

Reads OLLAMA_BASE_URL and OLLAMA_MODEL from environment.
All functions are best-effort — if Ollama is unavailable or returns
malformed output, they log a warning and return None so the calling
code can fall back to deterministic behaviour.
"""
import json
import logging
import os
import re
from typing import Optional

import requests

log = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen3:latest")
_TIMEOUT        = int(os.getenv("OLLAMA_TIMEOUT", "30"))


def is_configured() -> bool:
    return bool(OLLAMA_BASE_URL)


def is_available() -> bool:
    """Quick health check — returns True if Ollama responds."""
    if not OLLAMA_BASE_URL:
        return False
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.ok
    except Exception:
        return False


def generate(prompt: str, model: Optional[str] = None, expect_json: bool = False) -> Optional[str]:
    """
    Send a prompt to Ollama and return the response text.
    Returns None on any error.
    """
    if not OLLAMA_BASE_URL:
        return None
    m = model or OLLAMA_MODEL
    payload: dict = {
        "model":  m,
        "prompt": prompt,
        "stream": False,
    }
    if expect_json:
        payload["format"] = "json"
    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception as e:
        log.warning("Ollama generate failed (model=%s): %s", m, e)
        return None


def normalize_ingredient_names(names: list[str]) -> dict[str, str]:
    """
    Use the LLM to canonicalise a list of ingredient name strings into
    a consistent short form, collapsing common aliases:

      "boneless skinless chicken breast" → "chicken breast"
      "yellow onion"                     → "onion"
      "parmigiano-reggiano"              → "parmesan"

    Returns a dict mapping each input name → normalised name.
    Falls back to identity mapping on any failure.
    """
    if not names or not OLLAMA_BASE_URL:
        return {n: n for n in names}

    items_json = json.dumps(names, ensure_ascii=False)
    prompt = f"""You are a culinary ingredient canonicaliser.
Given this JSON array of ingredient names, return a JSON object mapping
each name to its canonical short form. Rules:
- Collapse modifiers: "boneless skinless chicken breast" → "chicken breast"
- Collapse colour/variety when interchangeable: "yellow onion" → "onion"
- Collapse brand/regional names: "parmigiano-reggiano" → "parmesan"
- Keep meaningful distinctions: "cherry tomato" ≠ "roma tomato"
- Keep distinctions between fresh/dried: "fresh basil" ≠ "dried basil"
- Output ONLY a valid JSON object, no markdown, no explanation.

Input: {items_json}
Output:"""

    raw = generate(prompt, expect_json=True)
    if not raw:
        return {n: n for n in names}

    try:
        # Strip possible markdown fences
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        mapping = json.loads(clean)
        if not isinstance(mapping, dict):
            raise ValueError("not a dict")
        # Ensure every input has a mapping; unknown → identity
        result = {}
        for name in names:
            canon = mapping.get(name, name)
            result[name] = canon if isinstance(canon, str) and canon.strip() else name
        log.info("Ollama normalised %d ingredient names", len(result))
        return result
    except Exception as e:
        log.warning("Ollama ingredient normalisation failed to parse: %s — raw=%r", e, raw[:200])
        return {n: n for n in names}


def normalize_for_bring(names: list[str]) -> dict[str, str]:
    """
    Use the LLM to map specialty ingredient names to their generic
    equivalents that are more likely to be found in Bring!'s article catalog.

    Examples:
      "herbes de provence"  → "mixed herbs"
      "demi-baguette"       → "baguette"
      "poulet brüstli"      → "chicken breast"
      "pancetta"            → "bacon"
      "creme fraiche"       → "sour cream"
      "arborio rice"        → "rice"

    Returns a dict mapping each input name → catalog-friendly name.
    Falls back to identity mapping on any failure or if Ollama is not configured.
    """
    if not names or not OLLAMA_BASE_URL:
        return {n: n for n in names}

    items_json = json.dumps(names, ensure_ascii=False)
    prompt = f"""You are a grocery catalog matcher for the Bring! shopping app.
Given ingredient names, return a JSON object mapping each to the most catalog-
friendly English name for that specific item.

Rules — KEEP specific varieties and types:
- "snow peas" → "snow peas" (NOT "peas" — different product)
- "roma tomatoes" → "roma tomatoes" (NOT "tomatoes" — specific variety)
- "cherry tomatoes" → "cherry tomatoes" (keep the type)
- "chicken thigh" → "chicken thigh" (NOT "chicken")
- "sweet potato" → "sweet potato" (NOT "potato")
- "red onion" → "red onion" (NOT "onion" — different flavor)
- "spring onion" → "spring onion" (different from onion)

Rules — simplify ONLY truly interchangeable verbose names:
- "boneless skinless chicken breast" → "chicken breast" (modifiers add nothing)
- "extra-virgin olive oil" → "olive oil" (grade not a catalog item)
- "herbes de provence" → "mixed herbs" (foreign name for common herb blend)
- "pancetta" → "bacon" (equivalent substitute)
- "arborio rice" → "arborio rice" (keep — it IS a specific type needed for risotto)
- "demi-baguette" → "baguette" (size modifier not catalog-relevant)
- "parmigiano-reggiano" → "parmesan" (brand/regional name → common name)

When in doubt, keep the original name unchanged.
- Output ONLY a valid JSON object, no markdown, no explanation.

Input: {items_json}
Output:"""

    raw = generate(prompt, expect_json=True)
    if not raw:
        return {n: n for n in names}

    try:
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        mapping = json.loads(clean)
        if not isinstance(mapping, dict):
            raise ValueError("not a dict")
        result = {}
        for name in names:
            mapped = mapping.get(name, name)
            result[name] = mapped if isinstance(mapped, str) and mapped.strip() else name
        log.info("Ollama Bring! normalisation: %d names → %d remapped",
                 len(names), sum(1 for k, v in result.items() if v != k))
        return result
    except Exception as e:
        log.warning("Ollama Bring! normalisation failed: %s — raw=%r", e, raw[:200])
        return {n: n for n in names}
