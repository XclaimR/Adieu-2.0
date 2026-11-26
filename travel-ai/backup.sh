#!/usr/bin/env bash
# Nightly from cron: dumps the production database to ~/backups/adieu, keeps the last 7 days.
set -euo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"  # cron's PATH lacks Docker Desktop's docker
cd "$(dirname "$0")"
dir="${BACKUP_DIR:-$HOME/backups/adieu}"
mkdir -p "$dir"
file="$dir/adieu-$(date +%F).sql.gz"
docker compose -p adieu-prod --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T postgres pg_dump -U postgres travel_ai | gzip > "$file.tmp"
mv "$file.tmp" "$file"
find "$dir" -name 'adieu-*.sql.gz' -mtime +6 -delete
