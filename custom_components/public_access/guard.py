"""Protections in front of the public page: who the visitor is, and session caps."""

from __future__ import annotations

from collections import Counter
from ipaddress import ip_address, ip_network

from aiohttp import web
from aiohttp.hdrs import X_FORWARDED_FOR

CF_CONNECTING_IP = "CF-Connecting-IP"


def proxy_not_trusted(request: web.Request) -> str | None:
    """Return the proxy's address when Home Assistant does not trust it.

    Behind Cloudflare or a reverse proxy the visitor's address travels in
    X-Forwarded-For. When `http: use_x_forwarded_for` and `trusted_proxies` are
    set, Home Assistant replaces `request.remote` with an address from that
    header; when they are not, `request.remote` stays the proxy's. Home
    Assistant then bans the proxy itself after a few failed logins, and every
    visitor behind it gets 403.
    """
    header = request.headers.get(X_FORWARDED_FOR)
    if not header or not request.remote:
        return None
    forwarded = [part.strip() for part in header.split(",") if part.strip()]
    if request.remote not in forwarded:
        return request.remote
    # Two proxies in a row (Cloudflare, then NPM) with only the inner one
    # trusted: Home Assistant settles on Cloudflare's address, which is in the
    # header, so the check above passes although visitors still share it.
    if _is_cloudflare(request.remote):
        return request.remote
    return None


# https://www.cloudflare.com/ips/ — the proxy most owners put in front.
_CLOUDFLARE = [
    ip_network(net)
    for net in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22", "2400:cb00::/32",
        "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
        "2a06:98c0::/29", "2c0f:f248::/32",
    )
]


def _is_cloudflare(address: str) -> bool:
    try:
        ip = ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in _CLOUDFLARE)


class SessionLimiter:
    """Caps concurrent public websocket sessions, overall and per address.

    Each session holds a subscription on Home Assistant, so the cap is what
    stops a crowd (or one visitor with a hundred tabs) from loading it.
    """

    def __init__(self, total: int, per_client: int) -> None:
        self._total = total
        self._per_client = per_client
        self._clients: Counter[str] = Counter()

    @property
    def active(self) -> int:
        return sum(self._clients.values())

    def acquire(self, client: str | None) -> bool:
        key = client or "unknown"
        if self.active >= self._total or self._clients[key] >= self._per_client:
            return False
        self._clients[key] += 1
        return True

    def release(self, client: str | None) -> None:
        key = client or "unknown"
        self._clients[key] -= 1
        if self._clients[key] <= 0:
            del self._clients[key]


def visitor_address(request: web.Request) -> str | None:
    """The visitor's own address, with no proxy settings needed in Home Assistant.

    Caps and rate limits must count visitors, not the proxy they share. Home
    Assistant only resolves the real address when every proxy in front of it is
    listed in its trusted proxies, which few owners set up. So:

    * a connection from Cloudflare carries the visitor in CF-Connecting-IP,
      which Cloudflare sets itself and a visitor cannot forge through it;
    * a connection from the owner's own network is their reverse proxy (NPM):
      the last X-Forwarded-For entry is the address it saw, and when that is
      Cloudflare, CF-Connecting-IP again names the visitor.
    """
    remote = request.remote
    if remote is None:
        return None
    if _is_cloudflare(remote):
        return request.headers.get(CF_CONNECTING_IP, remote).strip()
    if _is_private(remote):
        header = request.headers.get(X_FORWARDED_FOR, "")
        hops = [part.strip() for part in header.split(",") if part.strip()]
        if hops:
            last = hops[-1]
            if _is_cloudflare(last):
                return request.headers.get(CF_CONNECTING_IP, last).strip()
            return last
    return remote


def _is_private(address: str) -> bool:
    try:
        ip = ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def visitor_source(request: web.Request) -> str:
    """Which rule of visitor_address applied, for diagnostics."""
    remote = request.remote or ""
    if _is_cloudflare(remote):
        return "cloudflare"
    if _is_private(remote) and request.headers.get(X_FORWARDED_FOR):
        return "local_proxy"
    return "direct"
