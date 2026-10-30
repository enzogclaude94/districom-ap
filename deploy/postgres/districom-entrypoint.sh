#!/bin/sh
# Entry point of the production database (ADR 013): PostgreSQL does not start unless the backup disk is the one
# prepared by deploy/bin/backup-disk-init. Docker runs this on every start, including its restart policy after a
# reboot of the server or of Docker itself, so a backup disk that failed to mount stops the database instead of
# letting it run with backups going to the system disk. A disk lost while the database runs is the monitor's
# job (deploy/bin/monitor): the database keeps running and the alert is critical.
# Throwaway restores (deploy/bin/restore-drill) set DISTRICOM_REQUIRE_BACKUP_DISK=0.
set -e
marker=/var/lib/pgbackrest/.districom-backup-disk
if [ "${DISTRICOM_REQUIRE_BACKUP_DISK:-1}" != 0 ] && ! grep -q '^prepared=' "$marker" 2> /dev/null; then
    echo "FATAL: backup disk not mounted or not prepared ($marker missing): PostgreSQL will not start." >&2
    echo "Mount the backup disk (deploy/bin/backup-disk-init on a new one) and start again (docs/OPERATIONS.md)." >&2
    exit 78
fi
exec docker-entrypoint.sh "$@"
