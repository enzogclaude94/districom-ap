#!/usr/bin/env bash
# S1 acceptance tests of the server infrastructure (ADR 013), on a throwaway stack with fictitious data only.
#   deploy/tests/run-s1.sh            build images with `deploy/bin/dc build`, then test
#   SKIP_BUILD=1 deploy/tests/run-s1.sh   use images already tagged districom/*:current
# Needs Docker, curl, dig, openssl, python3 and uv (for the data generator of backend/perf).
# Refuses to run where a real .env exists: it writes .env, deploy/pgbackrest.env and deploy/offsite.env.
# Leaves its report in deploy/state/s1-test-report.txt; `deploy/tests/run-s1.sh clean` removes the stack.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="${S1_TEST_ROOT:-/tmp/districom-s1}"
CERTS="$ROOT/deploy/tests/.certs"
export DISTRICOM_COMPOSE_EXTRA="$ROOT/deploy/tests/docker-compose.test.yml"
export DRILL_DOCKER_ARGS="-v $CERTS:/certs:ro"
REPORT="$ROOT/deploy/state/s1-test-report.txt"
HOST=districom.s1.test

if [ -f "$ROOT/.env" ] && ! grep -q '^DISTRICOM_TEST_STACK=1$' "$ROOT/.env"; then
    echo "a real .env exists here: refusing to run the tests on this machine" >&2
    exit 1
fi

if [ "${1:-}" = clean ]; then
    [ -f "$ROOT/.env" ] && "$ROOT/deploy/bin/dc" --profile dns --profile ops down -v --remove-orphans
    docker rm -f districom-drill > /dev/null 2>&1
    docker network rm s1-lan s1-outside > /dev/null 2>&1
    mountpoint -q "$TEST_ROOT/backup" && umount "$TEST_ROOT/backup"
    rm -rf "$TEST_ROOT" "$CERTS" "$ROOT/.env" "$ROOT/deploy/pgbackrest.env" "$ROOT/deploy/offsite.env"
    exit 0
fi

mkdir -p "$ROOT/deploy/state"
# Monitor state left by an earlier run (how long a repository has been behind) would skew this one.
rm -f "$ROOT"/deploy/state/wal-behind-repo*
: > "$REPORT"
pass=0 failed=0
result() { # result PASS|FAIL "name" "detail"
    printf '%-4s  %s%s\n' "$1" "$2" "${3:+  — $3}" | tee -a "$REPORT"
    if [ "$1" = PASS ]; then pass=$((pass + 1)); else failed=$((failed + 1)); fi
}
expect() { # expect "name" command...   (PASS when the command succeeds)
    local name="$1"; shift
    local out
    if out="$("$@" 2>&1)"; then result PASS "$name"; else result FAIL "$name" "$(echo "$out" | tail -n 3 | tr '\n' ' ')"; fi
}
section() { printf '\n## %s\n' "$*" | tee -a "$REPORT"; }
secret() { openssl rand -hex 24; }
q() { "$ROOT/deploy/bin/dc" exec -T -u postgres db psql -XtAq -U districom -d districom_perf_s1 -c "$1"; }

# ---------------------------------------------------------------- setup
section "Setup (fictitious data, test certificates, S3 stand-in)"
mkdir -p "$CERTS" "$TEST_ROOT"
if [ ! -f "$CERTS/ca.crt" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=S1 test CA" -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2> /dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=s3test" -keyout "$CERTS/s3test.key" -out "$CERTS/s3test.csr" 2> /dev/null
    openssl x509 -req -in "$CERTS/s3test.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial -days 30 \
        -extfile <(printf 'subjectAltName=DNS:s3test') -out "$CERTS/s3test.crt" 2> /dev/null
    chmod 644 "$CERTS"/*
fi
if [ ! -f "$ROOT/.env" ]; then
    sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" -e "s|^SECRET_KEY=.*|SECRET_KEY=$(secret)|" \
        -e "s|^POSTGRES_DB=.*|POSTGRES_DB=districom_perf_s1|" -e "s|^DISTRICOM_HOST=.*|DISTRICOM_HOST=$HOST|" \
        -e "s|^CADDY_TLS_MODE=.*|CADDY_TLS_MODE=internal|" -e "s|^BACKUP_ROOT=.*|BACKUP_ROOT=$TEST_ROOT/backup|" \
        -e "s|^LAN_SUBNET=.*|LAN_SUBNET=10.99.0.0/24|" \
        "$ROOT/deploy/production.env.example" > "$ROOT/.env"
    echo "DISTRICOM_TEST_STACK=1" >> "$ROOT/.env"
    # The backup "disk" of the tests is a tmpfs mounted below; where mounting is not allowed, the mount point
    # check is relaxed (test environments only; the marker of deploy/bin/backup-disk-init is still required).
    mkdir -p "$TEST_ROOT/backup"
    if ! mountpoint -q "$TEST_ROOT/backup" && ! mount -t tmpfs -o size=6g,mode=755 s1-backup-disk "$TEST_ROOT/backup" 2> /dev/null; then
        echo "BACKUP_REQUIRE_MOUNTPOINT=0" >> "$ROOT/.env"
    fi
    cat > "$ROOT/deploy/pgbackrest.env" << EOF
PGBACKREST_REPO1_CIPHER_PASS=$(secret)
PGBACKREST_REPO2_TYPE=s3
PGBACKREST_REPO2_PATH=/districom
PGBACKREST_REPO2_S3_BUCKET=districom-s1
PGBACKREST_REPO2_S3_ENDPOINT=s3test
PGBACKREST_REPO2_STORAGE_PORT=9000
PGBACKREST_REPO2_STORAGE_CA_FILE=/certs/ca.crt
PGBACKREST_REPO2_S3_REGION=us-east-1
PGBACKREST_REPO2_S3_URI_STYLE=path
PGBACKREST_REPO2_S3_KEY=s1test
PGBACKREST_REPO2_S3_KEY_SECRET=s1test-secret-key-0123456789
PGBACKREST_REPO2_CIPHER_TYPE=aes-256-cbc
PGBACKREST_REPO2_CIPHER_PASS=$(secret)
PGBACKREST_REPO2_RETENTION_FULL_TYPE=time
PGBACKREST_REPO2_RETENTION_FULL=90
EOF
    obscure() { docker run --rm rclone/rclone:1.70 obscure "$1"; }
    cat > "$ROOT/deploy/offsite.env" << EOF
RCLONE_CONFIG_OFFSITE_TYPE=s3
RCLONE_CONFIG_OFFSITE_PROVIDER=Other
RCLONE_CONFIG_OFFSITE_ENDPOINT=https://s3test:9000
RCLONE_CONFIG_OFFSITE_REGION=us-east-1
RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID=s1test
RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY=s1test-secret-key-0123456789
RCLONE_CONFIG_OFFSITECRYPT_TYPE=crypt
RCLONE_CONFIG_OFFSITECRYPT_REMOTE=offsite:districom-s1-files/files
RCLONE_CONFIG_OFFSITECRYPT_PASSWORD=$(obscure "$(secret)")
RCLONE_CONFIG_OFFSITECRYPT_PASSWORD2=$(obscure "$(secret)")
EOF
fi
# shellcheck source=deploy/bin/lib.sh
source "$ROOT/deploy/bin/lib.sh"
# lib.sh turns on errexit for the scripts; the tests record failures and go on.
set +e

# ---------------------------------------------------------------- T0 static
section "T0 Static checks"
expect "shellcheck of every script" shellcheck -x "$ROOT"/deploy/bin/* "$ROOT"/deploy/host/*.sh "$ROOT"/deploy/rclone/*.sh "$ROOT"/deploy/tests/run-s1.sh
expect "production compose file is valid" docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" -f "$ROOT/deploy/docker-compose.prod.yml" config -q
prod_ports="$(docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" -f "$ROOT/deploy/docker-compose.prod.yml" config --format json \
    | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; print(" ".join(sorted(n for n,v in s.items() if v.get("ports"))))')"
expect "in production only caddy publishes ports (got: $prod_ports)" test "$prod_ports" = caddy
expect "development compose unchanged and valid" docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" config -q
expect "Caddyfile valid (internal TLS)" docker run --rm -e DISTRICOM_HOST=$HOST -e CADDY_TLS_MODE=internal districom/caddy:current caddy validate --config /etc/caddy/Caddyfile
expect "Caddyfile valid (cloudflare DNS-01)" docker run --rm -e DISTRICOM_HOST=$HOST -e CADDY_TLS_MODE=cloudflare -e CLOUDFLARE_API_TOKEN=s1-dummy-token-0123456789abcdefghijklmnopq districom/caddy:current caddy validate --config /etc/caddy/Caddyfile
expect "Tailscale policy parses (HuJSON)" python3 -c 'import re,json,sys; s=open(sys.argv[1]).read(); s=re.sub(r"^\s*//.*$","",s,flags=re.M); json.loads(re.sub(r",(\s*[}\]])",r"\1",s))' "$ROOT/deploy/tailscale/policy.hujson"
expect "host install script (dry run)" "$ROOT/deploy/host/install.sh" --dry-run

# ---------------------------------------------------------------- T1a backup disk guard
section "T1a Backup disk: nothing runs on a folder that is not the prepared, mounted backup disk"
nodisk="$TEST_ROOT/no-disk"
mkdir -p "$nodisk"
expect "up refuses it and writes nothing there" sh -c "! BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/up' --no-build && [ -z \"\$(ls -A '$nodisk')\" ]"
expect "backup refuses it" sh -c "! BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/backup' full"
expect "dump refuses it" sh -c "! BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/dump'"
expect "files-sync refuses it" sh -c "! BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/files-sync'"
expect "restore drill from repo1 refuses it" sh -c "! BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/restore-drill' --repo 1"
expect "backup-disk-init refuses a folder that is not a mount point" sh -c "! BACKUP_REQUIRE_MOUNTPOINT=1 BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/backup-disk-init'"
mkdir -p "$TEST_ROOT/stale/pgbackrest"
expect "backup-disk-init refuses backup folders that already exist without a marker" sh -c "! BACKUP_REQUIRE_MOUNTPOINT=0 BACKUP_ROOT='$TEST_ROOT/stale' '$ROOT/deploy/bin/backup-disk-init'"
if mountpoint -q "$TEST_ROOT/backup"; then disk_kind="a real mount point (tmpfs)"; else disk_kind="mount point check relaxed: this machine cannot mount"; fi
expect "backup-disk-init prepares the test backup disk ($disk_kind)" "$ROOT/deploy/bin/backup-disk-init"

# ---------------------------------------------------------------- T1 install
section "T1 First start (deploy/bin/up): images, db, stanza in both repos, migrations, app, first full backup, smoke"
dc up -d s3test
sleep 2
dc exec -T s3test mkdir -p /data/districom-s1 /data/districom-s1-files
if [ "${SKIP_BUILD:-0}" = 1 ]; then up_args=(--no-build); export UPDATE_BUILD_SERVICES="backend frontend"; else up_args=(); fi
expect "deploy/bin/up" "$ROOT/deploy/bin/up" "${up_args[@]}"
expect "repo1 and repo2 both hold a full backup" sh -c "[ \"\$('$ROOT/deploy/bin/dc' exec -T -u postgres db pgbackrest --stanza=districom info --output=json | python3 -c 'import json,sys; print(sorted({b[\"database\"][\"repo-key\"] for b in json.load(sys.stdin)[0][\"backup\"] if b[\"type\"]==\"full\"}))')\" = '[1, 2]' ]"
expect "repo2 content is encrypted (no PostgreSQL file names in clear)" sh -c "! '$ROOT/deploy/bin/dc' exec -T s3test sh -c 'find /data/districom-s1 -type f | head -200 | xargs cat' | grep -aq 'PG_VERSION'"

# ---------------------------------------------------------------- T2 exposure
section "T2 Exposure: what answers and where"
published="$(docker ps --filter label=com.docker.compose.project=districom --format '{{.Label "com.docker.compose.service"}} {{.Ports}}' | awk '/->/ {print $1}' | sort | tr '\n' ' ')"
expect "running stack publishes only caddy (+ the test-only db port) — got: $published" test "$published" = "caddy db "
expect "backend port 8000 not reachable from the host" sh -c "! curl -s --noproxy "*" --max-time 3 http://127.0.0.1:8000/api/v1/health"
expect "frontend port 3000 not reachable from the host" sh -c "! curl -s --noproxy "*" --max-time 3 http://127.0.0.1:3000/"
expect "unknown Host over HTTP gets no answer" sh -c "! curl -s --noproxy "*" --max-time 5 -H 'Host: evil.example' http://127.0.0.1/ -o /dev/null"
expect "unknown name over HTTPS: TLS refused" sh -c "! curl -sk --noproxy "*" --max-time 5 --resolve evil.example:443:127.0.0.1 https://evil.example/"
expect "bare IP over HTTPS: TLS refused" sh -c "! curl -sk --noproxy "*" --max-time 5 https://127.0.0.1/"
expect "caddy is published on IPv4 only ($(docker port "$(dc ps -q caddy)" | tr '\n' ' '))" sh -c "! docker port \"\$('$ROOT/deploy/bin/dc' ps -q caddy)\" | grep -v '^[0-9]*/tcp -> 0\.0\.0\.0:'"
expect "an IPv6 CADDY_BIND_IP is refused" sh -c "! CADDY_BIND_IP=:: '$ROOT/deploy/bin/dc' config -q"
cert_cn="$(echo | openssl s_client -connect 127.0.0.1:443 -servername $HOST 2> /dev/null | openssl x509 -noout -ext subjectAltName 2> /dev/null | tr -d ' ' | tail -n1)"
expect "certificate names $HOST ($cert_cn)" sh -c "echo '$cert_cn' | grep -q 'DNS:$HOST'"

# ---------------------------------------------------------------- T5 data (before login tests: creates the admin)
section "T5 Fictitious data (backend/perf generator through the app's own API)"
pw="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ROOT/.env")"
gen_log="$ROOT/deploy/state/s1-generator.log"
expect "generator: 20 business days of Rendiciones" env -i PATH="$PATH" HOME="$HOME" sh -c "cd '$ROOT/backend' && PERF_DATABASE_URL='postgresql+psycopg://districom:$pw@127.0.0.1:55432/districom_perf_s1' uv run --frozen python -m perf.generate --scale 1 --workers 1 --days 20 --seed 11 > '$gen_log' 2>&1"
entries="$(q 'SELECT count(*) FROM settlements.ledger_entries')"
expect "ledger has fictitious entries ($entries)" test "${entries:-0}" -gt 100

# ---------------------------------------------------------------- T3/T4 HTTPS, cookies, real IP
section "T3 HTTPS session and T4 real client IP"
jar="$TEST_ROOT/cookies.txt"
headers="$TEST_ROOT/login-headers.txt"
caddy_curl -o /dev/null -D "$headers" -c "$jar" -H 'Content-Type: application/json' -H 'X-Requested-With: s1' \
    -H 'X-Forwarded-For: 203.0.113.66' \
    --data '{"email":"perf-admin@districom.local","password":"perf-password-123"}' "https://$HOST/api/v1/auth/login"
expect "login over HTTPS through Caddy (200)" grep -q '^HTTP/[0-9.]* 200' "$headers"
expect "session cookie is Secure, HttpOnly, SameSite=Lax" sh -c "grep -i '^set-cookie:' '$headers' | grep -i secure | grep -i httponly | grep -qi 'samesite=lax'"
expect "authenticated API call with the cookie" caddy_curl -f -b "$jar" -o /dev/null "https://$HOST/api/v1/auth/me"
expect "mutating call without the CSRF header is refused (403)" sh -c "[ \"\$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --cacert '$STATE_DIR/caddy-local-root.crt' --resolve $HOST:443:127.0.0.1 -b '$jar' -X POST https://$HOST/api/v1/auth/logout)\" = 403 ]"
gateway="$(docker network inspect districom_default -f '{{(index .IPAM.Config 0).Gateway}}')"
session_ip="$(q 'SELECT ip FROM public.user_sessions ORDER BY id DESC LIMIT 1')"
expect "session records the client IP seen by Caddy ($session_ip = gateway $gateway), not Caddy's, not a spoofed header" test "$session_ip" = "$gateway"
dc exec -T frontend wget -q -O /dev/null --header 'Content-Type: application/json' --header 'X-Requested-With: s1' \
    --header 'X-Forwarded-For: 198.51.100.77' --post-data '{"email":"perf-admin@districom.local","password":"perf-password-123"}' \
    http://backend:8000/api/v1/auth/login 2> /dev/null
frontend_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(dc ps -q frontend)")"
direct_ip="$(q 'SELECT ip FROM public.user_sessions ORDER BY id DESC LIMIT 1')"
expect "X-Forwarded-For from anyone but Caddy is ignored ($direct_ip = frontend $frontend_ip)" test "$direct_ip" = "$frontend_ip"

# ---------------------------------------------------------------- T6 WAL to off-site, RPO
section "T6 Continuous WAL archiving to the off-site repository (RPO)"
q "CREATE SCHEMA IF NOT EXISTS s1test; CREATE TABLE IF NOT EXISTS s1test.markers (id serial PRIMARY KEY, label text, at timestamptz DEFAULT now())"
for i in 1 2 3; do
    q "INSERT INTO s1test.markers (label) VALUES ('rpo-$i')"
    segment="$(q 'SELECT pg_walfile_name(pg_current_wal_insert_lsn())')"
    start=$(date +%s)
    until dc exec -T -u postgres db pgbackrest --stanza=districom --repo=2 repo-ls "archive/districom/17-1/${segment:0:16}" 2> /dev/null | grep -q "^$segment"; do
        [ $(($(date +%s) - start)) -lt 300 ] || break
        sleep 3
    done
    took=$(($(date +%s) - start))
    expect "commit $i reached the off-site repository in ${took}s (archive_timeout 60 s; target ≤ 900 s)" test "$took" -lt 300
done

# ---------------------------------------------------------------- T7 restore drills
section "T7 Restore drills (throwaway database, compared with production)"
expect "drill from repo1 (backup disk): identical" "$ROOT/deploy/bin/restore-drill" --repo 1
expect "drill from repo2 ONLY (off-site, backup disk not mounted): identical" "$ROOT/deploy/bin/restore-drill" --repo 2 --network districom_default
latest_drill="$(find "$STATE_DIR/drills" -maxdepth 1 -name '*-repo2' | sort | tail -n1)"
rto="$(sed -n 's/.*restore_seconds=\([0-9]*\).*/\1/p' "$latest_drill/summary.txt")"
result PASS "measured restore time from off-site with this dataset: ${rto}s (RTO of the data part only)"

# ---------------------------------------------------------------- T7b fingerprint sensitivity
section "T7b The comparison sees any change in any business table (and only real changes)"
expect "drill from repo1 kept for the checks below: identical, pg_amcheck ok" "$ROOT/deploy/bin/restore-drill" --repo 1 --keep
drill_dir="$(find "$STATE_DIR/drills" -maxdepth 1 -name '*-repo1' | sort | tail -n1)"
expect "summary records pg_amcheck ok and every table compared ($(grep restored_tables "$drill_dir/summary.txt"))" grep -q 'amcheck=ok' "$drill_dir/summary.txt"
dq() { docker exec -u postgres districom-drill psql -XtAq -U districom -d districom_perf_s1 -c "SET session_replication_role = replica; $1"; }
drill_fp() { docker exec -i -u postgres districom-drill psql -X -U districom -d districom_perf_s1 -f - < "$ROOT/deploy/sql/verify.sql"; }
changed_tables() { drill_fp | diff "$drill_dir/production.txt" - | awk '/^[<>]/ {print $2, $3}' | sort -u | tr '\n' ' '; }
dq "VACUUM FULL settlements.expenses; CLUSTER settlements.ledger_entries USING ledger_entries_pkey" > /dev/null 2>&1
expect "rewriting tables (VACUUM FULL, CLUSTER) changes nothing" sh -c "[ -z \"$(changed_tables)\" ]"
dq "UPDATE settlements.expenses SET description = coalesce(description, '') || '.' WHERE id = (SELECT min(id) FROM settlements.expenses)" > /dev/null
diff1="$(changed_tables)"
expect "one character of one expense: only settlements.expenses differs ($diff1)" test "$diff1" = "table settlements.expenses "
dq "DELETE FROM settlements.cheque_events WHERE id = (SELECT max(id) FROM settlements.cheque_events)" > /dev/null
diff2="$(changed_tables)"
expect "one cheque event deleted: that table appears too ($diff2)" sh -c "echo '$diff2' | grep -q 'table settlements.cheque_events'"
dq "UPDATE public.third_parties SET updated_at = updated_at + interval '1 microsecond' WHERE id = (SELECT min(id) FROM public.third_parties)" > /dev/null
diff3="$(changed_tables)"
expect "one microsecond in a platform table is seen ($diff3)" sh -c "echo '$diff3' | grep -q 'table public.third_parties'"
dq "UPDATE settlements.ledger_entries SET amount = amount + 0.01 WHERE id = (SELECT min(id) FROM settlements.ledger_entries)" > /dev/null
diff4="$(changed_tables)"
expect "one cent in the ledger: the table and that account's balance differ ($diff4)" sh -c "echo '$diff4' | grep -q 'table settlements.ledger_entries' && echo '$diff4' | grep -q 'balance '"
docker rm -f districom-drill > /dev/null; docker volume rm districom-drill-data > /dev/null

# ---------------------------------------------------------------- T8 point in time
section "T8 Point-in-time restore from off-site"
q "INSERT INTO s1test.markers (label) VALUES ('pitr-before')"
sleep 2
target="$(q "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') || '+00'")"
sleep 2
q "INSERT INTO s1test.markers (label) VALUES ('pitr-after')"
dc exec -T -u postgres db pgbackrest --stanza=districom check > /dev/null
expect "restore to $target from repo2" "$ROOT/deploy/bin/restore-drill" --repo 2 --network districom_default --target-time "$target" --keep
pitr="$(docker exec -u postgres districom-drill psql -XtAq -U districom -d districom_perf_s1 -c "SELECT string_agg(label, ',' ORDER BY id) FROM s1test.markers WHERE label LIKE 'pitr-%'")"
expect "restored copy has the row before the target and not the one after ($pitr)" test "$pitr" = pitr-before
docker rm -f districom-drill > /dev/null; docker volume rm districom-drill-data > /dev/null

# ---------------------------------------------------------------- T9 Internet cut
section "T9 Off-site unreachable (Internet cut): the app keeps working, WAL catches up"
dc stop s3test > /dev/null
q "INSERT INTO s1test.markers (label) VALUES ('during-cut')"
q "SELECT pg_switch_wal()" > /dev/null
# PostgreSQL hears of the failure when pgBackRest's archive-timeout (60 s) runs out.
sleep 90
during="$(q "SELECT count(*) FROM s1test.markers WHERE label='during-cut'")"
expect "database accepts writes during the cut ($during row)" test "$during" = 1
expect "app answers during the cut" caddy_curl -f -o /dev/null "https://$HOST/api/v1/health"
failed_after="$(q 'SELECT (last_failed_time > coalesce(last_archived_time, last_failed_time - interval '"'1 s'"'))::text FROM pg_stat_archiver')"
expect "archiver reports the failure" test "$failed_after" = true
behind="$("$ROOT/deploy/bin/monitor" 2> /dev/null | grep 'repo2: WAL')"
expect "monitor warns that repo2 is unreachable or behind ($behind)" sh -c "echo '$behind' | grep -Eq '^WARN .*(unreachable|behind)'"
dc start s3test > /dev/null
sleep 3
expect "after the cut, pgBackRest check passes (backlog archived)" dc exec -T -u postgres db pgbackrest --stanza=districom check
expect "drill from repo2 after the cut: identical (includes what was written during it)" "$ROOT/deploy/bin/restore-drill" --repo 2 --network districom_default

# ---------------------------------------------------------------- T10 files and dumps
section "T10 Files outside the database (Comercial imports) and logical dumps"
dc exec -T backend sh -c 'head -c 200000 /dev/urandom > /data/uploads/s1-sample-import.xlsx'
sum_src="$(dc exec -T backend sha256sum /data/uploads/s1-sample-import.xlsx | cut -d' ' -f1)"
expect "logical dump (deploy/bin/dump)" "$ROOT/deploy/bin/dump"
expect "files-sync (backup disk + off-site, encrypted)" "$ROOT/deploy/bin/files-sync"
expect "local copy of the imported file is identical" sh -c "[ \"\$(sha256sum '$TEST_ROOT/backup/files/uploads/s1-sample-import.xlsx' | cut -d' ' -f1)\" = '$sum_src' ]"
expect "off-site copy has no clear file names" sh -c "! '$ROOT/deploy/bin/dc' exec -T s3test find /data/districom-s1-files | grep -q s1-sample-import"
restored_sum="$(dc --profile ops run --rm -T --entrypoint sh files-sync -c 'rclone cat offsitecrypt:uploads/s1-sample-import.xlsx | sha256sum' 2> /dev/null | cut -d' ' -f1)"
expect "off-site copy decrypts to the same file" test "$restored_sum" = "$sum_src"
expect "dump is a valid pg_restore archive" sh -c "ls '$TEST_ROOT'/backup/dumps/districom-*.dump > /dev/null && '$ROOT/deploy/bin/dc' exec -T db sh -c 'pg_restore --list \$(ls -t /var/lib/districom-dumps/districom-*.dump | head -n1) > /dev/null'"

# ---------------------------------------------------------------- T11 monitor
section "T11 Monitor"
expect "deploy/bin/monitor reports no FAIL" "$ROOT/deploy/bin/monitor"
sed 's/^/      /' "$STATE_DIR/monitor.txt" | tee -a "$REPORT"
expect "monitor fails when the backup disk is not there" sh -c "BACKUP_ROOT='$nodisk' '$ROOT/deploy/bin/monitor' 2> /dev/null | grep -q '^FAIL  backup disk'"

# ---------------------------------------------------------------- T12 update and rollback
section "T12 Update to a new version with a migration, then rollback restoring the database"
cd "$ROOT"
# The branch to come back to, or the commit when the checkout is detached (a worktree, CI).
start_branch="$(git symbolic-ref -q --short HEAD || git rev-parse HEAD)"
start_ref="$(git rev-parse HEAD)"
head_rev="$(dc run --rm --no-deps -T backend alembic heads 2> /dev/null | awk '{print $1}' | tail -n1)"
q "SELECT 1" > /dev/null
psql_db -f - < "$ROOT/deploy/sql/verify.sql" > "$TEST_ROOT/before-update.txt"
git checkout --quiet -b s1-test-update-throwaway
cat > backend/migrations/versions/9999_s1_update_probe.py << EOF
"""S1 TEST ONLY (never merged): a migration to prove update and rollback."""

from alembic import op

revision = "9999_s1_update_probe"
down_revision = "$head_rev"


def upgrade() -> None:
    op.execute("CREATE TABLE public.s1_update_probe (id int)")


def downgrade() -> None:
    op.execute("DROP TABLE public.s1_update_probe")
EOF
git add backend/migrations/versions/9999_s1_update_probe.py
git -c user.email=s1@test -c user.name=s1 commit --quiet -m "test: S1 throwaway migration (never pushed)"
new_ref="$(git rev-parse HEAD)"
git checkout --quiet "$start_branch"
expect "deploy/bin/update to the new version (backup, migration, smoke)" "$ROOT/deploy/bin/update" "$new_ref"
expect "new migration applied" sh -c "[ \"\$('$ROOT/deploy/bin/dc' exec -T -u postgres db psql -XtAq -U districom -d districom_perf_s1 -c \"SELECT to_regclass('public.s1_update_probe') IS NOT NULL\")\" = t ]"
q "INSERT INTO s1test.markers (label) VALUES ('after-update')"
"$ROOT/deploy/bin/rollback" --restore-db < /dev/null > "$TEST_ROOT/rollback-report.txt" 2>&1
rc=$?
expect "rollback --restore-db alone only reports and stops (exit $rc)" test "$rc" = 3
expect "the report names the table with data recorded after the update" grep -q 's1test.markers: .* rows at the backup' "$TEST_ROOT/rollback-report.txt"
expect "nothing changed: still on the new version, its migration in place" sh -c "[ \"\$(git -C '$ROOT' rev-parse HEAD)\" = '$new_ref' ] && [ \"\$('$ROOT/deploy/bin/dc' exec -T -u postgres db psql -XtAq -U districom -d districom_perf_s1 -c \"SELECT to_regclass('public.s1_update_probe') IS NOT NULL\")\" = t ]"
expect "a wrong confirmation is refused" sh -c "'$ROOT/deploy/bin/rollback' --restore-db --confirm wrong-label < /dev/null; [ \$? = 3 ]"
label="$(cat "$STATE_DIR/rollback-backup-label")"
expect "rollback --restore-db --confirm $label" "$ROOT/deploy/bin/rollback" --restore-db --confirm "$label"
expect "code back at the previous version" test "$(git rev-parse HEAD)" = "$start_ref"
psql_db -f - < "$ROOT/deploy/sql/verify.sql" > "$TEST_ROOT/after-rollback.txt"
expect "database identical to before the update (fingerprint)" diff -u "$TEST_ROOT/before-update.txt" "$TEST_ROOT/after-rollback.txt"
safety="$(cat "$STATE_DIR/rollback-safety-label")"
"$ROOT/deploy/bin/restore-drill" --repo 1 --set "$safety" --keep > /dev/null 2>&1
kept="$(docker exec -u postgres districom-drill psql -XtAq -U districom -d districom_perf_s1 -c "SELECT count(*) FROM s1test.markers WHERE label = 'after-update'" 2> /dev/null)"
expect "what the rollback discarded is still in backup $safety ($kept row)" test "$kept" = 1
docker rm -f districom-drill > /dev/null; docker volume rm districom-drill-data > /dev/null
expect "a second database rollback of the same update is refused" sh -c "! '$ROOT/deploy/bin/rollback' --restore-db --confirm '$label' < /dev/null"
git checkout --quiet "$start_branch"
git branch -D s1-test-update-throwaway > /dev/null 2>&1
expect "WAL archiving works on the new timeline after the rollback" dc exec -T -u postgres db pgbackrest --stanza=districom check

# ---------------------------------------------------------------- T13 split DNS
section "T13 Optional split DNS (CoreDNS): one name, two answers"
DNS_LAN_BIND_IP=127.0.0.3 LAN_SERVER_IP=192.168.1.10 DNS_TAILSCALE_BIND_IP=127.0.0.4 TAILSCALE_SERVER_IP=100.101.102.103 \
    DNS_UPSTREAM=127.0.0.53 dc --profile dns up -d dns > /dev/null 2>&1
start=$(date +%s)
until [ -n "$(dig +short +time=1 +tries=1 @127.0.0.3 $HOST A)" ] || [ $(($(date +%s) - start)) -gt 30 ]; do sleep 1; done
docker logs "$(dc --profile dns ps -aq dns)" 2>&1 | tail -n 5 | sed 's/^/      coredns: /' | tee -a "$REPORT"
lan_answer="$(dig +short @127.0.0.3 $HOST A)"
ts_answer="$(dig +short @127.0.0.4 $HOST A)"
expect "LAN interface answers the LAN address ($lan_answer)" test "$lan_answer" = 192.168.1.10
expect "Tailscale interface answers the Tailscale address ($ts_answer)" test "$ts_answer" = 100.101.102.103
expect "Tailscale interface answers nothing else (no open resolver)" sh -c "[ -z \"\$(dig +short @127.0.0.4 example.com A)\" ]"
dc --profile dns stop dns > /dev/null 2>&1

# ---------------------------------------------------------------- T14 firewall
section "T14 Firewall: only the LAN subnet and Tailscale reach Caddy (two simulated networks)"
expect "a LAN_SUBNET of 0.0.0.0/0 is refused" sh -c "! LAN_SUBNET=0.0.0.0/0 '$ROOT/deploy/host/firewall.sh' apply"
expect "a public LAN_SUBNET is refused" sh -c "! LAN_SUBNET=8.8.8.0/24 '$ROOT/deploy/host/firewall.sh' apply"
# Two bridges whose names are not br-*: one is "the LAN" (10.99.0.0/24 = LAN_SUBNET), the other any other network.
docker network create -o com.docker.network.bridge.name=s1lan0 --subnet 10.99.0.0/24 s1-lan > /dev/null
docker network create -o com.docker.network.bridge.name=s1out0 --subnet 10.98.0.0/24 s1-outside > /dev/null
probe() { docker run --rm --network "$1" --entrypoint python districom/backend:current -c "import socket; socket.create_connection(('$2', $3), 4)" > /dev/null 2>&1; }
caddy_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(dc ps -q caddy)")"
backend_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(dc ps -q backend)")"
"$ROOT/deploy/host/firewall.sh" remove
expect "control, without the rules: another network DOES reach Caddy (what the rules are for)" probe s1-outside 10.98.0.1 443
expect "apply" env LAN_SUBNET=10.99.0.0/24 "$ROOT/deploy/host/firewall.sh" apply
expect "apply twice keeps one jump" sh -c "LAN_SUBNET=10.99.0.0/24 '$ROOT/deploy/host/firewall.sh' apply && [ \$(iptables -S DOCKER-USER | grep -c DISTRICOM-INGRESS) = 1 ]"
iptables -S DISTRICOM-INGRESS | sed 's/^/      /' | tee -a "$REPORT"
expect "the IPv6 chain drops every new connection to containers" sh -c "ip6tables -S DISTRICOM-INGRESS | grep -q -- '--ctstate NEW -j DROP'"
dropped_before="$(iptables -L DISTRICOM-INGRESS -v -x -n | awk '$3 == "DROP" {print $1}')"
expect "LAN reaches Caddy on 443" probe s1-lan 10.99.0.1 443
expect "LAN reaches Caddy on 80 (redirect to HTTPS)" probe s1-lan 10.99.0.1 80
expect "another network does not reach Caddy" sh -c "! docker run --rm --network s1-outside --entrypoint python districom/backend:current -c \"import socket; socket.create_connection(('10.98.0.1', 443), 4)\" > /dev/null 2>&1"
expect "another network does not reach Caddy through the LAN address either" sh -c "! docker run --rm --network s1-outside --entrypoint python districom/backend:current -c \"import socket; socket.create_connection(('10.99.0.1', 443), 4)\" > /dev/null 2>&1"
expect "LAN cannot route straight to the backend container ($backend_ip:8000)" sh -c "! docker run --rm --network s1-lan --entrypoint python districom/backend:current -c \"import socket; socket.create_connection(('$backend_ip', 8000), 4)\" > /dev/null 2>&1"
expect "LAN cannot route straight to Caddy's container address ($caddy_ip:443), only through the published port" sh -c "! docker run --rm --network s1-lan --entrypoint python districom/backend:current -c \"import socket; socket.create_connection(('$caddy_ip', 443), 4)\" > /dev/null 2>&1"
dropped_after="$(iptables -L DISTRICOM-INGRESS -v -x -n | awk '$3 == "DROP" {print $1}')"
expect "those attempts were dropped by DISTRICOM-INGRESS ($dropped_before → $dropped_after packets)" test "$dropped_after" -gt "$dropped_before"
expect "HTTPS still served with the rules loaded" caddy_curl -f -o /dev/null "https://$HOST/api/v1/health"
expect "remove" "$ROOT/deploy/host/firewall.sh" remove
docker network rm s1-lan s1-outside > /dev/null

# ---------------------------------------------------------------- T15 archive queue overflow
section "T15 Long off-site outage that fills the archive queue: dropped WAL is never reported as a full recovery"
dc stop s3test > /dev/null
for i in $(seq 1 14); do
    q "INSERT INTO s1test.markers (label) VALUES ('overflow-$i')"
    q "SELECT pg_switch_wal()" > /dev/null
done
start=$(date +%s)
until [ -n "$("$ROOT/deploy/bin/wal-gap" 2> /dev/null)" ] || [ $(($(date +%s) - start)) -gt 600 ]; do sleep 10; done
gap="$("$ROOT/deploy/bin/wal-gap" 2> /dev/null)"
expect "pgBackRest dropped WAL after the queue (128 MiB in this test) filled ($gap)" test -n "$gap"
dc start s3test > /dev/null
sleep 3
expect "pgBackRest check passes again: on its own it would look healthy" dc exec -T -u postgres db pgbackrest --stanza=districom check
gap_line="$("$ROOT/deploy/bin/monitor" 2> /dev/null | grep 'WAL GAP')"
expect "monitor reports the gap as a FAIL ($gap_line)" sh -c "echo '$gap_line' | grep -q '^FAIL  WAL GAP'"
expect "smoke test fails while the gap is open" sh -c "! '$ROOT/deploy/bin/smoke'"
"$ROOT/deploy/bin/restore-drill" --repo 2 --network districom_default > "$TEST_ROOT/drill-gap.txt" 2>&1
rc=$?
expect "drill from repo2 across the gap: INCOMPLETE, not identical (exit $rc)" sh -c "[ $rc = 3 ] && grep -q 'result=INCOMPLETE' '$TEST_ROOT/drill-gap.txt'"
expect "the drill names the cause" grep -q 'recovery ended before configured recovery target was reached' "$TEST_ROOT/drill-gap.txt"
expect "remedy: a full backup in every repository" "$ROOT/deploy/bin/backup" full "after WAL gap"
expect "the gap is closed for restores after that backup" sh -c "[ -z \"\$('$ROOT/deploy/bin/wal-gap')\" ]"
expect "drill from repo2 after the remedy: identical" "$ROOT/deploy/bin/restore-drill" --repo 2 --network districom_default
expect "monitor reports no FAIL again" "$ROOT/deploy/bin/monitor"

# ---------------------------------------------------------------- T16 database start without the backup disk
section "T16 The database does not start without the prepared backup disk, at any start (also Docker's own restarts)"
db_id="$(dc ps -q db)"
fatal_count() { docker logs "$db_id" 2>&1 | grep -c 'backup disk not mounted or not prepared'; }
dc stop db > /dev/null 2>&1
# What a reboot with the backup disk missing looks like: nothing (or an empty disk) at the mount point.
if mountpoint -q "$TEST_ROOT/backup"; then
    mount -t tmpfs -o size=16m s1-empty-disk "$TEST_ROOT/backup"; hide="overmount"
else
    mv "$TEST_ROOT/backup/pgbackrest/$BACKUP_MARKER" "$TEST_ROOT/backup-inner-marker"; hide="marker"
fi
dc start db > /dev/null 2>&1
sleep 15
expect "PostgreSQL refuses to start ($(fatal_count) refusals logged)" sh -c "[ $(fatal_count) -ge 1 ] && ! docker exec '$db_id' pg_isready -q"
expect "deploy/bin/up refuses too" sh -c "! '$ROOT/deploy/bin/up' --no-build"
if [ -n "${S1_DOCKER_RESTART_CMD:-}" ]; then
    before="$(fatal_count)"
    sh -c "$S1_DOCKER_RESTART_CMD" > /dev/null 2>&1
    until docker info > /dev/null 2>&1; do sleep 1; done
    sleep 20
    after="$(fatal_count)"
    expect "after a restart of Docker, its restart policy starts the database again and it refuses again ($before → $after)" sh -c "[ $after -gt $before ] && ! docker exec '$db_id' pg_isready -q"
    expect "the rest of the stack came back (Caddy running)" sh -c "docker ps --format '{{.Names}}' | grep -q caddy"
else
    result FAIL "Docker restart not exercised (set S1_DOCKER_RESTART_CMD)"
fi
if [ "$hide" = overmount ]; then umount "$TEST_ROOT/backup"; else mv "$TEST_ROOT/backup-inner-marker" "$TEST_ROOT/backup/pgbackrest/$BACKUP_MARKER"; fi
dc up -d --no-build s3test db > /dev/null 2>&1
docker restart "$db_id" > /dev/null 2>&1
wait_healthy db
expect "with the disk back, the database starts" docker exec "$db_id" pg_isready -q
dc up -d --no-build > /dev/null 2>&1
wait_healthy backend
expect "and the stack passes its smoke test" "$ROOT/deploy/bin/smoke"

# ---------------------------------------------------------------- T17 backup disk lost while running
section "T17 Backup disk lost while the database runs: it keeps running, the alert is critical and says what protects the data"
mv "$TEST_ROOT/backup/$BACKUP_MARKER" "$TEST_ROOT/backup-root-marker"
"$ROOT/deploy/bin/monitor" > "$TEST_ROOT/monitor-degraded.txt" 2> /dev/null
critical="$(head -n1 "$TEST_ROOT/monitor-degraded.txt")"
expect "first line is the critical alert ($critical)" sh -c "echo '$critical' | grep -q '^FAIL  CRITICAL: BACKUP PROTECTION DEGRADED'"
expect "it reports both repositories and that the RPO holds only through repo2" sh -c "echo '$critical' | grep -q 'repo1: ' && echo '$critical' | grep -q 'RPO kept only through repo2'"
expect "the database keeps running and the app answers" sh -c "docker exec '$db_id' pg_isready -q && '$ROOT/deploy/bin/smoke'"
dc stop s3test > /dev/null
"$ROOT/deploy/bin/monitor" > "$TEST_ROOT/monitor-degraded.txt" 2> /dev/null
critical="$(head -n1 "$TEST_ROOT/monitor-degraded.txt")"
expect "with repo2 down too, it says the RPO is not met ($critical)" sh -c "echo '$critical' | grep -q 'RPO NOT MET'"
dc start s3test > /dev/null
mv "$TEST_ROOT/backup-root-marker" "$TEST_ROOT/backup/$BACKUP_MARKER"
sleep 3
expect "with the disk back, no critical line" sh -c "! '$ROOT/deploy/bin/monitor' 2> /dev/null | grep -q CRITICAL"

# ---------------------------------------------------------------- summary
printf '\n%s passed, %s failed\n' "$pass" "$failed" | tee -a "$REPORT"
[ "$failed" -eq 0 ]
