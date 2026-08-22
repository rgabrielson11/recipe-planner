#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Recipe Planner — deploy script
#
# Usage (run from the repo root):
#   ./deploy.sh [deploy_dir]
#
#   deploy_dir defaults to the directory this script lives in (i.e. the
#   repo root on the Unraid host).  When using the git+docker-compose
#   workflow, simply:
#
#       cd /mnt/user/appdata/recipe-planner
#       git pull origin master
#       ./deploy.sh
#
# What it does, in order:
#   1. Backs up the SQLite database and hand-edited YAML configs
#   2. Stops the running container
#   3. Rebuilds the image from the updated source
#   4. Starts the container
#   5. Tails logs briefly to confirm a clean startup
#
# The entrypoint.sh baked into the image handles YAML syncing automatically
# on every start — recipe_sources.yaml is always updated from the image,
# and user-owned YAMLs are only initialised if missing.
#
# NOTE: If you are using the Unraid Community Applications template (GHCR
# image), you do NOT need this script — just pull a new image from the
# Docker tab and the entrypoint handles everything.  This script is for
# the git+docker-compose (Option A) workflow only.
# ─────────────────────────────────────────────────────────────────────────────

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$DEPLOY_DIR/backups/$TIMESTAMP"

echo "== Recipe Planner deploy =="
echo "Deploy dir: $DEPLOY_DIR"
echo "Backup dir: $BACKUP_DIR"
echo

if [ ! -f "$DEPLOY_DIR/docker-compose.yml" ]; then
  echo "ERROR: $DEPLOY_DIR doesn't look like the Recipe Planner repo (no docker-compose.yml)." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
cd "$DEPLOY_DIR"

echo "-- 1/4 Backing up database and YAML configs --"
if [ -f "data/db/recipe_planner.db" ]; then
  cp -v "data/db/recipe_planner.db" "$BACKUP_DIR/recipe_planner.db"
  [ -f "data/db/recipe_planner.db-wal" ] && cp -v "data/db/recipe_planner.db-wal" "$BACKUP_DIR/"
  [ -f "data/db/recipe_planner.db-shm" ] && cp -v "data/db/recipe_planner.db-shm" "$BACKUP_DIR/"
  echo "   DB backed up ($(du -h "$BACKUP_DIR/recipe_planner.db" | cut -f1))"
else
  echo "   No database found — skipping (first deploy?)"
fi

if [ -d "backend/app/data" ]; then
  cp -r "backend/app/data" "$BACKUP_DIR/yaml-config"
  echo "   YAML configs backed up"
else
  echo "   No YAML config directory found — skipping"
fi

echo
echo "-- 2/4 Stopping container --"
docker compose down

echo
echo "-- 3/4 Rebuilding and starting --"
docker compose up -d --build

echo
echo "-- 4/4 Tailing logs (15s) --"
sleep 3
if command -v timeout >/dev/null 2>&1; then
  timeout 15 docker logs -f recipe-planner 2>&1 || true
else
  docker logs --tail 50 recipe-planner
fi

echo
echo "== Deploy complete =="
echo "Backup saved at: $BACKUP_DIR"
echo
echo "Rollback if needed:"
echo "  docker compose down"
echo "  cp \"$BACKUP_DIR/recipe_planner.db\" \"$DEPLOY_DIR/data/db/recipe_planner.db\""
echo "  cp -r \"$BACKUP_DIR/yaml-config/.\" \"$DEPLOY_DIR/backend/app/data/\""
echo "  git checkout HEAD~1 && docker compose up -d --build"
