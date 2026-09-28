"""Protections in front of the public page: proxy sanity and session caps."""

from __future__ import annotations

from collections import Counter

from aiohttp import web
from aiohttp.hdrs import X_FORWARDED_FOR


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
    forwarded = {part.strip() for part in header.split(",") if part.strip()}
    if request.remote in forwarded:
        return None
    return request.remote


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
