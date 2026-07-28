#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Recipe Planner — deploy script (Patch 13)
#
# Usage:
#   ./deploy.sh [deploy_dir]
#
#   deploy_dir defaults to /mnt/user/appdata/recipe-planner.  Run this script
#   from inside the extracted zip — it expects a "recipe-planner-phase9/"
#   folder to sit right next to it.  The live deployment directory (with
#   docker-compose.yml, data/db/, backend/app/data/) is assumed to already
#   exist there; this script does not create a deployment from scratch.
#
# What it does, in order:
#   1. Stops the recipe-planner container
#   2. Backs up the SQLite database (+ WAL/SHM sidecar files) and the
#      hand-edited YAML configs, timestamped under deploy_dir/backups/
#   3. Copies the patched code over the deployment directory
#   4. Restores your customized YAMLs (pantry_staples, package_sizes,
#      rejection_reasons, cooking_vocabulary) from the backup — NOT
#      recipe_sources.yaml, since this patch's changes live there
#   5. Rebuilds and restarts the container
#   6. Tails logs briefly so you can confirm a clean startup
#
# Nothing here deletes anything — the backup directory is left in place and
# a rollback command is printed at the end.
# ─────────────────────────────────────────────────────────────────────────────

DEPLOY_DIR="${1:-/mnt/user/appdata/recipe-planner}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="$SCRIPT_DIR/recipe-planner-phase9"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$DEPLOY_DIR/backups/$TIMESTAMP"

echo "== Recipe Planner deploy =="
echo "Deploy dir:  $DEPLOY_DIR"
echo "Payload dir: $PAYLOAD_DIR"
echo "Backup dir:  $BACKUP_DIR"
echo

if [ ! -d "$PAYLOAD_DIR" ]; then
  echo "ERROR: expected patch payload at $PAYLOAD_DIR" >&2
  echo "       Run this script from inside the extracted zip (recipe-planner-phase9/ should be a sibling)." >&2
  exit 1
fi
if [ ! -f "$DEPLOY_DIR/docker-compose.yml" ]; then
  echo "ERROR: $DEPLOY_DIR doesn't look like the Recipe Planner deployment (no docker-compose.yml found)." >&2
  echo "       Pass the correct path: ./deploy.sh /path/to/recipe-planner" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
cd "$DEPLOY_DIR"

echo "-- 1/6 Stopping container --"
docker compose down

echo
echo "-- 2/6 Backing up database --"
if [ -f "data/db/recipe_planner.db" ]; then
  cp -v "data/db/recipe_planner.db" "$BACKUP_DIR/recipe_planner.db"
  # WAL mode (Patch 12) may leave -wal/-shm sidecar files with uncommitted data
  [ -f "data/db/recipe_planner.db-wal" ] && cp -v "data/db/recipe_planner.db-wal" "$BACKUP_DIR/"
  [ -f "data/db/recipe_planner.db-shm" ] && cp -v "data/db/recipe_planner.db-shm" "$BACKUP_DIR/"
  echo "   DB backed up ($(du -h "$BACKUP_DIR/recipe_planner.db" | cut -f1))"
else
  echo "   No existing database found at data/db/recipe_planner.db — skipping (first deploy?)"
fi

echo
echo "-- 3/6 Backing up YAML configs --"
if [ -d "backend/app/data" ]; then
  cp -rv "backend/app/data" "$BACKUP_DIR/yaml-config"
else
  echo "   No existing config directory found — skipping (first deploy?)"
fi

echo
echo "-- 4/6 Copying patched files into place --"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.bak' "$PAYLOAD_DIR/" "$DEPLOY_DIR/"
else
  echo "   rsync not found — falling back to cp -a"
  cp -a "$PAYLOAD_DIR/." "$DEPLOY_DIR/"
  find "$DEPLOY_DIR" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "$DEPLOY_DIR" -name "*.pyc" -delete 2>/dev/null || true
fi

echo
echo "-- 5/6 Restoring your customized YAMLs (recipe_sources.yaml intentionally excluded) --"
if [ -d "$BACKUP_DIR/yaml-config" ]; then
  for f in pantry_staples.yaml package_sizes.yaml rejection_reasons.yaml cooking_vocabulary.yaml; do
    if [ -f "$BACKUP_DIR/yaml-config/$f" ]; then
      cp -v "$BACKUP_DIR/yaml-config/$f" "backend/app/data/$f"
    fi
  done
else
  echo "   Nothing to restore (first deploy)"
fi

echo
echo "-- 6/6 Rebuilding and starting --"
docker compose up -d --build

echo
echo "== Deploy complete =="
echo "Backup saved at: $BACKUP_DIR"
echo
echo "Tailing logs for 15s (Ctrl-C to stop early)..."
if command -v timeout >/dev/null 2>&1; then
  timeout 15 docker logs -f recipe-planner || true
else
  docker logs --tail 40 recipe-planner
fi

echo
echo "Done. Check the app in your browser (e.g. http://<server-ip>:8111)."
echo
echo "Rollback if something looks wrong:"
echo "  docker compose down"
echo "  cp \"$BACKUP_DIR/recipe_planner.db\" \"$DEPLOY_DIR/data/db/recipe_planner.db\""
echo "  cp -r \"$BACKUP_DIR/yaml-config/.\" \"$DEPLOY_DIR/backend/app/data/\""
echo "  # then re-extract the previous patch's zip over $DEPLOY_DIR before:"
echo "  docker compose up -d --build"
