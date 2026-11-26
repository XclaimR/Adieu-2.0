#!/usr/bin/env bash
# Pull main, rebuild and restart Adieu in production. Run on the server.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans
docker image prune -f
