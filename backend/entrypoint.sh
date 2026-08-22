#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Recipe Planner — container entrypoint
#
# Runs before uvicorn on every container start (including after a Docker
# image pull / update).  Syncs YAML config files from the image's baked-in
# defaults into the mounted data directory so the running container always
# has current config — without clobbering the user's own customisations.
#
# Two tiers of YAML:
#
#   CODE-OWNED  (always overwritten from image)
#     recipe_sources.yaml — HelloFresh / Home Chef source list, scrape budget,
#                           non-dinner keyword filter, background job settings.
#                           Managed by the app; user edits belong in the UI,
#                           not by hand-editing this file.
#
#   USER-OWNED  (copied from image on first run only)
#     pantry_staples.yaml      — household always-on-hand list
#     package_sizes.yaml       — retail package sizes for shopping rounding
#     rejection_reasons.yaml   — rejection vocabulary and permanence flags
#     cooking_vocabulary.yaml  — cooking methods, cookware, skill levels
# ─────────────────────────────────────────────────────────────────────────────

DEFAULTS=/app/defaults
DATA=/app/app/data

echo "[entrypoint] Syncing YAML configs..."

# ── Code-owned: always update from image ─────────────────────────────────────
for f in recipe_sources.yaml; do
    if [ -f "$DEFAULTS/$f" ]; then
        if [ -f "$DATA/$f" ]; then
            if ! diff -q "$DEFAULTS/$f" "$DATA/$f" > /dev/null 2>&1; then
                echo "[entrypoint] Updating $f from image defaults"
                cp "$DEFAULTS/$f" "$DATA/$f"
            else
                echo "[entrypoint] $f is already up to date"
            fi
        else
            echo "[entrypoint] Installing $f (first run)"
            cp "$DEFAULTS/$f" "$DATA/$f"
        fi
    fi
done

# ── User-owned: only copy if missing ─────────────────────────────────────────
for f in pantry_staples.yaml package_sizes.yaml rejection_reasons.yaml cooking_vocabulary.yaml; do
    if [ -f "$DEFAULTS/$f" ] && [ ! -f "$DATA/$f" ]; then
        echo "[entrypoint] Installing $f (first run)"
        cp "$DEFAULTS/$f" "$DATA/$f"
    fi
done

echo "[entrypoint] YAML sync complete. Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8111
