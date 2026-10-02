#!/usr/bin/env bash
# Compressed logical backup of the compose database.
#   scripts/backup.sh [output-dir]   (default: ./backups)
# Schedule daily via cron on the host; keep 14 daily + 8 weekly; copy off-host.
set -euo pipefail
out_dir="${1:-./backups}"
mkdir -p "$out_dir"
file="$out_dir/districom-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$file"
echo "Backup written: $file"
