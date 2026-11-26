#!/usr/bin/env bash
# Nightly from cron: dumps the database to ~/backups, keeps the last 7 days.
set -euo pipefail
cd "$(dirname "$0")"
dir="${BACKUP_DIR:-$HOME/backups}"
mkdir -p "$dir"
file="$dir/adieu-$(date +%F).sql.gz"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U postgres travel_ai | gzip > "$file.tmp"
mv "$file.tmp" "$file"
find "$dir" -name 'adieu-*.sql.gz' -mtime +6 -delete
