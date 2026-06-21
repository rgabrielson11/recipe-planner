"""
Loads and writes the human-editable YAML config files in app/data/.

Uses ruamel.yaml's round-trip mode so that comments and formatting survive
even when the app itself writes to a file (e.g. POST /pantry/staples).
That means a person can hand-edit these files directly in VS Code (e.g.
through the bind-mounted backend/app directory) at any time, and the API
can also write to the same files, without one clobbering the other's
comments.

No caching — every read opens the file fresh, so edits made outside the
app take effect on the very next request with no restart required.
"""

from pathlib import Path
from threading import Lock

from ruamel.yaml import YAML

DATA_DIR = Path(__file__).resolve().parent / "data"
_lock = Lock()

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=2, offset=0)


def _path(filename: str) -> Path:
    return DATA_DIR / filename


def load_yaml(filename: str):
    with open(_path(filename)) as f:
        return _yaml.load(f)


def save_yaml(filename: str, data) -> None:
    with _lock:
        with open(_path(filename), "w") as f:
            _yaml.dump(data, f)


# ---------- Pantry staples ----------

def get_staples() -> list[str]:
    data = load_yaml("pantry_staples.yaml")
    return list(data.get("staples", []))


def add_staple(name: str) -> list[str]:
    data = load_yaml("pantry_staples.yaml")
    staples = data.setdefault("staples", [])
    normalized = name.strip().lower()
    if normalized and normalized not in [s.lower() for s in staples]:
        staples.append(normalized)
        save_yaml("pantry_staples.yaml", data)
    return list(staples)


def remove_staple(name: str) -> list[str]:
    data = load_yaml("pantry_staples.yaml")
    staples = data.get("staples", [])
    normalized = name.strip().lower()
    for i in reversed([i for i, s in enumerate(staples) if s.lower() == normalized]):
        del staples[i]
    save_yaml("pantry_staples.yaml", data)
    return list(staples)


# ---------- Cooking vocabulary (interview options) ----------

def get_cooking_vocabulary() -> dict:
    return dict(load_yaml("cooking_vocabulary.yaml"))


# ---------- Package sizes (shopping list rounding) ----------

def get_package_sizes() -> dict:
    return dict(load_yaml("package_sizes.yaml"))


# ---------- Rejection reasons ----------

def get_rejection_reasons() -> list[str]:
    data = load_yaml("rejection_reasons.yaml")
    return list(data.get("reasons", []))
