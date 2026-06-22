"""
Loads and writes the human-editable YAML config files in app/data/.

Uses ruamel.yaml round-trip mode so comments survive writes from the API.
No caching — every read opens the file fresh so VS Code edits take effect
immediately without a container restart.
"""

from pathlib import Path
from threading import Lock
from typing import Any

from ruamel.yaml import YAML

DATA_DIR = Path(__file__).resolve().parent / "data"
_lock    = Lock()

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=2, offset=0)


def _path(filename: str) -> Path:
    return DATA_DIR / filename


def load_yaml(filename: str) -> Any:
    with open(_path(filename)) as f:
        return _yaml.load(f)


def save_yaml(filename: str, data: Any) -> None:
    with _lock:
        with open(_path(filename), "w") as f:
            _yaml.dump(data, f)


# ── Pantry staples ────────────────────────────────────────────────────────────

def get_staples() -> list[str]:
    data = load_yaml("pantry_staples.yaml")
    return list(data.get("staples", []))


def add_staple(name: str) -> list[str]:
    data = load_yaml("pantry_staples.yaml")
    staples    = data.setdefault("staples", [])
    normalized = name.strip().lower()
    if normalized and normalized not in [s.lower() for s in staples]:
        staples.append(normalized)
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


# ── Cooking vocabulary (interview options) ────────────────────────────────────

def get_cooking_vocabulary() -> dict:
    return dict(load_yaml("cooking_vocabulary.yaml"))


# ── Package sizes (shopping list rounding) ────────────────────────────────────

def get_package_sizes() -> dict:
    return dict(load_yaml("package_sizes.yaml"))


# ── Rejection reasons ─────────────────────────────────────────────────────────

def get_rejection_reasons() -> list[dict]:
    """
    Returns the full structured rejection reason list from rejection_reasons.yaml.
    Each entry is a dict with keys:
      key            str   — machine identifier used in the API
      label          str   — human-readable display string
      permanent      bool  — True = exclude recipe forever;
                             False = suppress for suppress_weeks then resurface
      suppress_weeks int   — only present when permanent=False
    """
    data = load_yaml("rejection_reasons.yaml")
    reasons = []
    for r in data.get("reasons", []):
        entry: dict = {
            "key":       r["key"],
            "label":     r["label"],
            "permanent": bool(r.get("permanent", True)),
        }
        if not entry["permanent"]:
            entry["suppress_weeks"] = int(r.get("suppress_weeks", 2))
        reasons.append(entry)
    return reasons


def get_rejection_reason(key: str) -> dict | None:
    """Returns one rejection reason dict by key, or None if not found."""
    for r in get_rejection_reasons():
        if r["key"] == key:
            return r
    return None
