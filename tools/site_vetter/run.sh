#!/bin/sh
# Recipe site vetter — runs in a throwaway container built from the
# recipe-planner image, with the repo's backend/app mounted read-only so it
# tests with the live code + recipe_sources.yaml.
#   ./run.sh                          # all supported sites
#   ./run.sh --hosts budgetbytes.com  # specific sites
# Results: tools/site_vetter/out/{report.md,results.json,suggested_sources.yaml}
REPO=/mnt/user/appdata/recipe-planner
docker rm -f recipe-site-vetter >/dev/null 2>&1
docker run -d --name recipe-site-vetter \
  --entrypoint python \
  -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$REPO/tools/site_vetter:/vet" \
  -v "$REPO/backend/app:/app/app:ro" \
  ghcr.io/rgabrielson11/recipe-planner:latest \
  /vet/vet_sites.py "$@"
echo "Started. Follow with: docker logs -f recipe-site-vetter"
