#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Recipe Planner — container entrypoint
#
# Runs before uvicorn on every container start (including after a Docker
# image pull / update).  Syncs YAML config files from the image's baked-in
# defaults into the mounted data directory.
#
# Two tiers of YAML:
#
#   CODE-OWNED  (always overwritten from image if changed)
#     recipe_sources.yaml — discovery sources, scrape budget, non-dinner
#                           filter, background job settings.
#
#   USER-OWNED  (copied from image only if missing OR empty)
#     pantry_staples.yaml      — household always-on-hand list
#     package_sizes.yaml       — retail package sizes for shopping rounding
#     rejection_reasons.yaml   — rejection vocabulary and permanence flags
#     cooking_vocabulary.yaml  — cooking methods, cookware, skill levels
# ─────────────────────────────────────────────────────────────────────────────

DEFAULTS=/app/defaults
DATA=/app/app/data

echo "[entrypoint] Syncing YAML configs..."
mkdir -p "$DATA"

# ── Code-owned: always update from image if content differs ──────────────────
for f in recipe_sources.yaml; do
    if [ -f "$DEFAULTS/$f" ]; then
        if [ -f "$DATA/$f" ] && diff -q "$DEFAULTS/$f" "$DATA/$f" > /dev/null 2>&1; then
            echo "[entrypoint] $f is already up to date"
        else
            echo "[entrypoint] Updating $f from image defaults"
            cp "$DEFAULTS/$f" "$DATA/$f"
        fi
    fi
done

# ── User-owned: copy if missing OR empty (empty = broken from failed init) ────
for f in pantry_staples.yaml package_sizes.yaml rejection_reasons.yaml cooking_vocabulary.yaml protein_categories.yaml; do
    if [ -f "$DEFAULTS/$f" ]; then
        # -s = file exists and has size > 0
        if [ ! -s "$DATA/$f" ]; then
            echo "[entrypoint] Installing $f (missing or empty)"
            cp "$DEFAULTS/$f" "$DATA/$f"
        fi
    fi
done

echo "[entrypoint] YAML sync complete. Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8111
