#!/usr/bin/env bash
# Pull main, rebuild and restart the production stack. Run from the production clone (see docs/DEPLOY.md).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env.prod ] || { echo "No .env.prod here: run this from the production clone, see docs/DEPLOY.md" >&2; exit 1; }
git pull --ff-only origin main
docker compose -p adieu-prod --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --build --remove-orphans
docker image prune -f
