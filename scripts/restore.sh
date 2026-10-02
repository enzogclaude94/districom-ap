#!/usr/bin/env bash
# Restore a dump into a NEW database to validate backups without touching production.
#   scripts/restore.sh backups/districom-YYYYMMDD-HHMMSS.dump [target-db]
# A backup that was never restored is not a backup strategy: run this monthly.
set -euo pipefail
dump="$1"
target="${2:-districom_restore_check}"
docker compose exec -T db sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists $target && createdb -U \"\$POSTGRES_USER\" $target"
docker compose exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $target --no-owner" < "$dump"
docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $target -c 'SELECT count(*) AS users FROM users;'"
echo "Restore check OK into database '$target'."
