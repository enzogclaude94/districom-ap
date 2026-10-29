#!/usr/bin/env bash
# Shared helpers of the DistriCom server scripts (docs/OPERATIONS.md). Sourced by deploy/bin/*, never run.
set -euo pipefail

ROOT="${DISTRICOM_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DISTRICOM_ROOT="$ROOT"
STATE_DIR="${STATE_DIR:-$ROOT/deploy/state}"
mkdir -p "$STATE_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Reads KEY=VALUE files the way Docker Compose does (no shell evaluation; values may contain spaces).
# Variables already set in the environment win, so a test or an operator can override one value.
load_env_file() {
    local file="$1" line key value
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in '' | '#'*) continue ;; esac
        key="${line%%=*}"
        value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        [ -n "${!key+x}" ] || export "$key=$value"
    done < "$file"
}

load_env_file "$ROOT/.env"
: "${POSTGRES_USER:=districom}" "${POSTGRES_DB:=districom}" "${BACKUP_ROOT:=/srv/districom-backup}"
: "${DISTRICOM_HOST:?DISTRICOM_HOST is not set (.env)}"
export POSTGRES_USER POSTGRES_DB BACKUP_ROOT

# docker compose with the production override. DISTRICOM_COMPOSE_EXTRA adds files (used by deploy/tests only).
dc() {
    local args=(--project-directory "$ROOT" -f "$ROOT/docker-compose.yml" -f "$ROOT/deploy/docker-compose.prod.yml")
    local extra
    for extra in ${DISTRICOM_COMPOSE_EXTRA:-}; do args+=(-f "$extra"); done
    docker compose "${args[@]}" "$@"
}

pgbr() { dc exec -T -u postgres db pgbackrest --stanza=districom "$@"; }
psql_db() { dc exec -T -u postgres db psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"; }

# Repositories configured in the db container: 1 always, 2 when deploy/pgbackrest.env defines it.
repos() {
    echo 1
    if dc exec -T db printenv PGBACKREST_REPO2_TYPE > /dev/null 2>&1; then echo 2; fi
}

wait_healthy() {
    local service="$1" timeout="${2:-180}" waited=0 id status
    id="$(dc ps -q "$service")"
    [ -n "$id" ] || die "service $service is not running"
    while :; do
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
        case "$status" in healthy | running) return 0 ;; esac
        [ "$waited" -lt "$timeout" ] || die "$service not healthy after ${timeout}s (status: $status)"
        sleep 2; waited=$((waited + 2))
    done
}

# curl to the stack through Caddy, as a client of the LAN would, whatever DNS says on the server itself.
caddy_curl() {
    local ca=()
    if [ "${CADDY_TLS_MODE:-cloudflare}" = internal ]; then
        dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > "$STATE_DIR/caddy-local-root.crt"
        ca=(--cacert "$STATE_DIR/caddy-local-root.crt")
    fi
    curl -sS --noproxy "*" --max-time 20 --resolve "$DISTRICOM_HOST:443:127.0.0.1" --resolve "$DISTRICOM_HOST:80:127.0.0.1" "${ca[@]}" "$@"
}

# update and rollback check out another version of this very directory: they first re-run themselves from a
# private copy so Bash never reads a script that Git is rewriting.
run_from_copy() {
    [ -n "${DISTRICOM_RUNNING_COPY:-}" ] && return 0
    local copy
    copy="$(mktemp -d)"
    cp -a "$ROOT/deploy/bin/." "$copy/"
    DISTRICOM_RUNNING_COPY=1 exec bash "$copy/$(basename "$1")" "${@:2}"
}

# Image tags of a release: `current` runs, `rollback` is what ran before the last update.
# shellcheck disable=SC2034 # used by update and rollback
IMAGES=(districom/db districom/backend districom/frontend districom/caddy)
