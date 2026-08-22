# auto-added logger
"""
Loads and writes the human-editable YAML config files in app/data/.
Uses ruamel.yaml round-trip mode so comments survive API writes.
Reads fresh on every call — VS Code edits take effect immediately.
"""

import logging
from pathlib import Path
from threading import Lock
from typing import Any

from ruamel.yaml import YAML

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
_lock    = Lock()

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=2, offset=0)


def _path(filename: str) -> Path:
    return DATA_DIR / filename


def _to_plain(obj) -> object:
    """
    Recursively convert ruamel.yaml CommentedMap / CommentedSeq objects to
    plain Python dicts and lists so FastAPI can JSON-serialize them.

    dict() only converts the top level; nested structures stay as ruamel.yaml
    types which cause silent serialisation failures (empty responses).
    """
    if hasattr(obj, "items"):                          # dict-like
        return {str(k): _to_plain(v) for k, v in obj.items()}
    elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        return [_to_plain(i) for i in obj]            # list-like
    else:
        return obj                                     # scalar — str, int, float, bool, None


def load_yaml(filename: str) -> Any:
    """
    Load a YAML file with ruamel.yaml (round-trip mode).
    Falls back to PyYAML safe_load if ruamel raises a non-YAML exception —
    this guards against ruamel round-trip bugs ('string index out of range', etc.)
    while still allowing ruamel to write comments on save.

    Always returns a dict (never None) so callers can safely call .get() on
    the result.  An empty or null YAML file returns {}.
    """
    result = None
    try:
        with open(_path(filename)) as f:
            result = _yaml.load(f)
    except Exception as ruamel_err:
        # ruamel.yaml can raise bare Python exceptions (IndexError, etc.) on
        # certain YAML constructs it wrote itself. Fall back to PyYAML which
        # is more lenient, then log so the issue can be investigated.
        import yaml as _pyyaml
        log.warning(
            "ruamel.yaml failed loading %s (%s) — falling back to PyYAML",
            filename, ruamel_err,
        )
        with open(_path(filename)) as f:
            result = _pyyaml.safe_load(f)
    if result is None:
        log.warning("load_yaml: %s parsed as None (empty file?) — returning {}", filename)
        return {}
    return result


def save_yaml(filename: str, data: Any) -> None:
    with _lock:
        with open(_path(filename), "w") as f:
            _yaml.dump(data, f)


# ── Pantry staples ────────────────────────────────────────────────

def get_staples() -> list[str]:
    return list(load_yaml("pantry_staples.yaml").get("staples", []))


def add_staple(name: str) -> list[str]:
    data    = load_yaml("pantry_staples.yaml")
    staples = data.setdefault("staples", [])
    norm    = name.strip().lower()
    if norm and norm not in [s.lower() for s in staples]:
        staples.append(norm)
        save_yaml("pantry_staples.yaml", data)
    return list(staples)


def remove_staple(name: str) -> list[str]:
    data    = load_yaml("pantry_staples.yaml")
    staples = data.get("staples", [])
    norm    = name.strip().lower()
    for i in reversed([i for i, s in enumerate(staples) if s.lower() == norm]):
        del staples[i]
    save_yaml("pantry_staples.yaml", data)
    return list(staples)


# ── Cooking vocabulary ────────────────────────────────────────────

def get_cooking_vocabulary() -> dict:
    return dict(load_yaml("cooking_vocabulary.yaml"))


# ── Package sizes ─────────────────────────────────────────────────

def get_package_sizes() -> dict:
    return dict(load_yaml("package_sizes.yaml"))


# ── Rejection reasons ─────────────────────────────────────────────

def get_rejection_reasons() -> list[dict]:
    reasons = []
    for r in load_yaml("rejection_reasons.yaml").get("reasons", []):
        entry = {"key": r["key"], "label": r["label"], "permanent": bool(r.get("permanent", True))}
        if not entry["permanent"]:
            entry["suppress_weeks"] = int(r.get("suppress_weeks", 2))
        reasons.append(entry)
    return reasons


def get_rejection_reason(key: str) -> dict | None:
    return next((r for r in get_rejection_reasons() if r["key"] == key), None)


# ── Recipe sources (discovery engine) ────────────────────────────

def get_recipe_sources() -> dict:
    """Returns the full recipe_sources.yaml as a plain Python dict."""
    return _to_plain(load_yaml("recipe_sources.yaml"))


def get_discovery_config() -> dict:
    """Returns only the discovery settings block."""
    return _to_plain(get_recipe_sources().get("discovery", {}))


def get_enabled_sources() -> list[dict]:
    """Returns only the enabled source entries."""
    return [s for s in get_recipe_sources().get("sources", []) if s.get("enabled", True)]
