#!/bin/sh
# Copies files that live outside PostgreSQL (imported files of Comercial, nightly logical dumps) to the backup
# disk and, when deploy/offsite.env configures it, to the off-site bucket, encrypted by rclone crypt.
# `copy`, never `sync`: deleting a file on the server never deletes its backup. Files are write-once, so
# `--immutable` fails loudly instead of silently replacing a backup whose source changed.
set -eu

rclone copy /data/uploads /backup/files/uploads --immutable --log-level NOTICE
rclone copy /data/dumps /backup/files/dumps --exclude '*.partial' --immutable --log-level NOTICE

if [ -n "${RCLONE_CONFIG_OFFSITECRYPT_TYPE:-}" ]; then
    rclone copy /data/uploads offsitecrypt:uploads --immutable --log-level NOTICE
    rclone copy /data/dumps offsitecrypt:dumps --exclude '*.partial' --immutable --log-level NOTICE
    date +%s > /backup/state/files-sync-offsite.ok
fi
date +%s > /backup/state/files-sync-local.ok
