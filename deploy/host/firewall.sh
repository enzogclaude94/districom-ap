#!/usr/bin/env bash
# Who may reach the ports Docker publishes (80/443 of Caddy). Docker's published ports bypass ufw, so the
# rule lives in the DOCKER-USER chain, which Docker evaluates first (ADR 013):
#   from the LAN subnet: 80 and 443 · through Tailscale (tailscale0): 443 only · anything else: dropped.
# It also drops traffic routed straight to a container address (172.30.0.x), whatever the port.
# IPv6: Caddy is published on IPv4 only (deploy/bin/lib.sh refuses an IPv6 CADDY_BIND_IP); the same chain in
# ip6tables drops any new IPv6 connection to a container anyway. Traffic Docker hands to its userland proxy
# arrives on INPUT instead, where ufw (default deny, IPv4 and IPv6) applies.
# The router of the office forwards no port to the server; this is the second barrier.
#   deploy/host/firewall.sh apply | remove | show      (run by districom-firewall.service before Docker starts)
# LAN_SUBNET (required, a private IPv4 range) and LAN_INTERFACE (optional, e.g. enp3s0: LAN traffic must also
# arrive through that interface) come from the environment or from .env.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHAIN=DISTRICOM-INGRESS

from_env_file() { [ -f "$ROOT/.env" ] && sed -n "s/^$1=//p" "$ROOT/.env" | tail -n1; }
lan_subnet="${LAN_SUBNET:-$(from_env_file LAN_SUBNET || true)}"
lan_interface="${LAN_INTERFACE:-$(from_env_file LAN_INTERFACE || true)}"

# Only a private IPv4 range with a real prefix: 0.0.0.0/0 or a public range would open Caddy to everyone.
valid_lan_subnet() {
    python3 - "$1" << 'PY'
import ipaddress, sys
try:
    net = ipaddress.IPv4Network(sys.argv[1], strict=True)
except ValueError:
    sys.exit(1)
sys.exit(0 if net.is_private and net.prefixlen >= 8 and not net.is_loopback else 1)
PY
}

remove_family() {
    local ipt="$1"
    while "$ipt" -C DOCKER-USER -j "$CHAIN" 2> /dev/null; do "$ipt" -D DOCKER-USER -j "$CHAIN"; done
    "$ipt" -F "$CHAIN" 2> /dev/null || true
    "$ipt" -X "$CHAIN" 2> /dev/null || true
}

remove() {
    remove_family iptables
    if command -v ip6tables > /dev/null 2>&1; then remove_family ip6tables; fi
}

# Common head of both chains: replies, and traffic that starts in a container (its own outgoing connections).
chain_head() {
    local ipt="$1"
    # Before Docker starts (districom-firewall.service runs first) DOCKER-USER may not exist yet: Docker keeps
    # a chain that already exists, so the filter is in place before any container publishes a port.
    "$ipt" -N DOCKER-USER 2> /dev/null || true
    "$ipt" -N "$CHAIN" 2> /dev/null || "$ipt" -F "$CHAIN"
    "$ipt" -C DOCKER-USER -j "$CHAIN" 2> /dev/null || "$ipt" -I DOCKER-USER 1 -j "$CHAIN"
    "$ipt" -A "$CHAIN" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
    "$ipt" -A "$CHAIN" -i docker0 -j RETURN
    "$ipt" -A "$CHAIN" -i br-+ -j RETURN
}

apply() {
    [ -n "$lan_subnet" ] || { echo "LAN_SUBNET is not set (.env)" >&2; exit 1; }
    valid_lan_subnet "$lan_subnet" || { echo "LAN_SUBNET '$lan_subnet' is not a private IPv4 range" >&2; exit 1; }
    local lan=(-s "$lan_subnet")
    [ -z "$lan_interface" ] || lan+=(-i "$lan_interface")

    chain_head iptables
    # New connections to published ports, matched on the ORIGINAL destination: after DNAT the port is the
    # container's, so a packet sent straight to a container address (no DNAT) never matches these lines.
    iptables -A "$CHAIN" -p tcp "${lan[@]}" -m conntrack --ctstate DNAT --ctorigdstport 80 -j RETURN
    iptables -A "$CHAIN" -p tcp "${lan[@]}" -m conntrack --ctstate DNAT --ctorigdstport 443 -j RETURN
    iptables -A "$CHAIN" -p tcp -i tailscale0 -m conntrack --ctstate DNAT --ctorigdstport 443 -j RETURN
    iptables -A "$CHAIN" -m conntrack --ctstate NEW -j DROP

    if command -v ip6tables > /dev/null 2>&1 && ip6tables -S > /dev/null 2>&1; then
        chain_head ip6tables
        ip6tables -A "$CHAIN" -m conntrack --ctstate NEW -j DROP
    fi
}

case "${1:-show}" in
    apply) remove; apply ;;
    remove) remove ;;
    show)
        iptables -S "$CHAIN" 2> /dev/null || echo "chain $CHAIN not present (IPv4)"
        ip6tables -S "$CHAIN" 2> /dev/null || echo "chain $CHAIN not present (IPv6)"
        ;;
    *) echo "usage: $0 apply|remove|show" >&2; exit 2 ;;
esac
