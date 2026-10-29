#!/usr/bin/env bash
# Who may reach the ports Docker publishes (80/443 of Caddy). Docker's published ports bypass ufw, so the
# rule lives in the DOCKER-USER chain, which Docker evaluates first (ADR 013):
#   from the LAN subnet: 80 and 443 · through Tailscale (tailscale0): 443 only · anything else: dropped.
# The router of the office forwards no port to the server; this is the second barrier.
#   deploy/host/firewall.sh apply | remove | show      (run by districom-firewall.service after Docker starts)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHAIN=DISTRICOM-INGRESS

lan_subnet="${LAN_SUBNET:-}"
if [ -z "$lan_subnet" ] && [ -f "$ROOT/.env" ]; then
    lan_subnet="$(sed -n 's/^LAN_SUBNET=//p' "$ROOT/.env" | tail -n1)"
fi

remove() {
    while iptables -C DOCKER-USER -j "$CHAIN" 2> /dev/null; do iptables -D DOCKER-USER -j "$CHAIN"; done
    iptables -F "$CHAIN" 2> /dev/null || true
    iptables -X "$CHAIN" 2> /dev/null || true
}

apply() {
    [ -n "$lan_subnet" ] || { echo "LAN_SUBNET is not set (.env)" >&2; exit 1; }
    iptables -N "$CHAIN" 2> /dev/null || iptables -F "$CHAIN"
    iptables -C DOCKER-USER -j "$CHAIN" 2> /dev/null || iptables -I DOCKER-USER 1 -j "$CHAIN"
    # Replies and the containers' own outgoing traffic.
    iptables -A "$CHAIN" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
    iptables -A "$CHAIN" -i docker0 -j RETURN
    iptables -A "$CHAIN" -i br-+ -j RETURN
    # New connections to published ports (after DNAT, so match the original destination port).
    iptables -A "$CHAIN" -p tcp -s "$lan_subnet" -m conntrack --ctstate NEW --ctorigdstport 80 -j RETURN
    iptables -A "$CHAIN" -p tcp -s "$lan_subnet" -m conntrack --ctstate NEW --ctorigdstport 443 -j RETURN
    iptables -A "$CHAIN" -p tcp -i tailscale0 -m conntrack --ctstate NEW --ctorigdstport 443 -j RETURN
    iptables -A "$CHAIN" -m conntrack --ctstate NEW -j DROP
}

case "${1:-show}" in
    apply) remove; apply ;;
    remove) remove ;;
    show) iptables -S "$CHAIN" 2> /dev/null || echo "chain $CHAIN not present" ;;
    *) echo "usage: $0 apply|remove|show" >&2; exit 2 ;;
esac
