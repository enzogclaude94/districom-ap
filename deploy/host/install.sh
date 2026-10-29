#!/usr/bin/env bash
# Prepares a fresh Ubuntu Server 24.04 LTS as the DistriCom server (docs/OPERATIONS.md, "Instalación").
# Run as root from the repository clone (recommended: /opt/districom). Idempotent.
#   deploy/host/install.sh [--dry-run]
# Before it: deploy/bin/init-env and the PENDING values of .env (LAN_SUBNET, BACKUP_ROOT...).
# It does NOT: create the RAID (done when installing Ubuntu), log Tailscale in, write secrets, or start the app.
# After it: tailscale up → deploy/bin/up.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dry=0
[ "${1:-}" = --dry-run ] && dry=1
run() { if [ "$dry" -eq 1 ]; then printf 'DRY  %s\n' "$*"; else "$@"; fi; }
step() { printf '\n== %s\n' "$*"; }

[ "$dry" -eq 1 ] || [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "WARNING: tested for Ubuntu Server 24.04 LTS, found ${PRETTY_NAME:-unknown}" >&2

lan_subnet="$(sed -n 's/^LAN_SUBNET=//p' "$ROOT/.env" 2> /dev/null | tail -n1)"
lan_subnet="${lan_subnet:-192.168.1.0/24}"

step "Base packages: time sync, firewall, security updates, disk health, UPS client"
run apt-get update
run apt-get install -y ca-certificates curl git gnupg jq openssl python3 ufw chrony unattended-upgrades \
    smartmontools nut-client
run systemctl enable --now chrony
# Security updates only, never an automatic reboot: a reboot is planned (docs/OPERATIONS.md).
run dpkg-reconfigure -f noninteractive unattended-upgrades

step "Docker Engine (official repository)"
if ! command -v docker > /dev/null 2>&1; then
    run install -m 0755 -d /etc/apt/keyrings
    run sh -c 'curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc'
    run sh -c ". /etc/os-release; echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \$VERSION_CODENAME stable\" > /etc/apt/sources.list.d/docker.list"
    run apt-get update
    run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
run systemctl enable --now docker

step "Tailscale (installed, not logged in: 'tailscale up' needs an approved auth key, see OPERATIONS.md)"
if ! command -v tailscale > /dev/null 2>&1; then
    run sh -c 'curl -fsSL https://tailscale.com/install.sh | sh'
fi
run systemctl enable --now tailscaled

step "Host firewall (ufw): SSH and DNS only from the LAN and Tailscale; Tailscale's own UDP port"
run ufw default deny incoming
run ufw default allow outgoing
run ufw allow from "$lan_subnet" to any port 22 proto tcp
run ufw allow in on tailscale0 to any port 22 proto tcp
run ufw allow from "$lan_subnet" to any port 53
run ufw allow in on tailscale0 to any port 53
run ufw allow 41641/udp
run ufw --force enable
# Ports 80/443 are published by Docker and filtered by districom-firewall.service (DOCKER-USER), not by ufw.

step "systemd units: firewall for Docker, backups, dump, files-sync, monitor"
for unit in "$ROOT"/deploy/systemd/*; do
    target="/etc/systemd/system/$(basename "$unit")"
    if [ "$dry" -eq 1 ]; then
        printf 'DRY  install %s -> %s (@ROOT@=%s)\n' "$unit" "$target" "$ROOT"
    else
        sed "s|@ROOT@|$ROOT|g" "$unit" > "$target"
    fi
done
run systemctl daemon-reload
run systemctl enable --now districom-firewall.service
for timer in districom-backup-full districom-backup-diff districom-dump districom-files-sync districom-monitor; do
    run systemctl enable --now "$timer.timer"
done

step "Backup disk"
backup_root="$(sed -n 's/^BACKUP_ROOT=//p' "$ROOT/.env" 2> /dev/null | tail -n1)"
backup_root="${backup_root:-/srv/districom-backup}"
if [ "$dry" -eq 0 ] && ! mountpoint -q "$backup_root"; then
    echo "WARNING: $backup_root is not a mount point. It must be the separate backup disk (OPERATIONS.md)." >&2
fi

cat << MSG

Done. Next (docs/OPERATIONS.md):
  1. tailscale up --advertise-tags=tag:districom-server --auth-key=<one-time key from the admin console>
     then put the server's 100.x address in .env (TAILSCALE_SERVER_IP) if the split DNS is used.
  2. deploy/bin/up
MSG
