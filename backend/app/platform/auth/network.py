"""Where a request comes from, for audit only (S2): never a reason to grant or deny anything.

The IP is the real client's: behind Caddy, uvicorn takes it from X-Forwarded-For only when the request comes from
Caddy itself (`--proxy-headers` with FORWARDED_ALLOW_IPS, ADR 013). Anyone can reach the app from a LAN address
through a misconfigured router, and a Tailscale address says nothing about the person: so the origin is recorded
and shown, and authorization stays with the session, the roles and the protected areas.
"""

import ipaddress
from typing import Final

from fastapi import Request

# Tailscale gives every device an address in the CGNAT range and in its own IPv6 prefix.
TAILSCALE_NETWORKS: Final = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def client_ip(request: Request) -> str | None:
    """The client's IP as uvicorn resolved it, or None when it is not an IP (e.g. the test client)."""
    host = request.client.host if request.client else None
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def access_origin(ip: str | None) -> str:
    """`local` (the server itself), `tailscale`, `lan` (any private address) or `other`."""
    if ip is None:
        return "other"
    address = ipaddress.ip_address(ip)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return "local"
    if any(address in network for network in TAILSCALE_NETWORKS):
        return "tailscale"
    if address.is_private:
        return "lan"
    return "other"
