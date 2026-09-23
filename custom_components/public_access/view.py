"""The public, unauthenticated HTTP surface.

Design rules enforced here:

* Only GET is implemented, so every other verb gets an automatic 405.
* The client never names an entity, a statistic or a date range. It may only pick a
  period from a closed enum; ids come from the sanitized dashboard and the energy
  preferences, resolved server-side.
* Nothing is served unless the integration is enabled and the licence may serve.
* `X-Frame-Options: SAMEORIGIN` is applied by Home Assistant's own middleware after
  this handler returns and cannot be overridden from here, so embedding the page in
  another site requires a header rewrite at the reverse proxy. CSP, X-Robots-Tag and
  Cache-Control do pass through and are set below.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CACHE_SECONDS,
    CONF_ENABLED,
    CONF_FRAME_ANCESTORS,
    CONF_NOINDEX,
    DEFAULT_CACHE_SECONDS,
    PERIODS,
    RATE_LIMIT_PER_MINUTE,
)
from .coordinator import PublicDashboardCoordinator

_LOGGER = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"


@lru_cache(maxsize=8)
def _asset(name: str) -> str:
    """Read a bundled asset once."""
    return (ASSETS / name).read_text(encoding="utf-8")


class RateLimiter:
    """Fixed-window per-IP limiter. Small and good enough for a public page."""

    def __init__(self, per_minute: int = RATE_LIMIT_PER_MINUTE) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, tuple[int, int]] = {}

    def allow(self, client: str | None) -> bool:
        key = client or "unknown"
        window = int(time.time() // 60)
        start, count = self._hits.get(key, (window, 0))
        if start != window:
            start, count = window, 0
        count += 1
        self._hits[key] = (start, count)
        if len(self._hits) > 4096:  # crude but bounded
            self._hits = {
                k: v for k, v in self._hits.items() if v[0] == window
            }
        return count <= self._per_minute


class PublicDashboardView(HomeAssistantView):
    """Serves one dashboard publicly, read-only."""

    requires_auth = False
    name = "public_access:dashboard"

    def __init__(
        self,
        hass: HomeAssistant,
        public_path: str,
        coordinator: PublicDashboardCoordinator,
    ) -> None:
        self.hass = hass
        self.public_path = public_path
        self.url = f"/{public_path}"
        self.extra_urls = [f"/{public_path}/{{extra:.+}}"]
        self._coordinator = coordinator
        self._limiter = RateLimiter()

    # -- helpers ---------------------------------------------------------------

    @property
    def _options(self) -> dict[str, Any]:
        return self._coordinator.options

    def _decorate(self, response: web.Response) -> web.Response:
        options = self._options
        max_age = int(options.get(CONF_CACHE_SECONDS, DEFAULT_CACHE_SECONDS))
        response.headers["Cache-Control"] = f"public, max-age={max_age}"
        if options.get(CONF_NOINDEX, True):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        ancestors = options.get(CONF_FRAME_ANCESTORS) or "'self'"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            f"frame-ancestors {ancestors}"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _json(self, payload: Any, status: int = 200) -> web.Response:
        body = json.dumps(payload, default=str)
        response = web.Response(
            body=body.encode(), content_type="application/json", status=status
        )
        response.headers["ETag"] = hashlib.sha256(body.encode()).hexdigest()[:32]
        return self._decorate(response)

    def _unavailable(self, message: str) -> web.Response:
        html = _asset("unavailable.html").replace("{{message}}", message)
        response = web.Response(text=html, content_type="text/html", status=503)
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- routing ---------------------------------------------------------------

    async def get(self, request: web.Request, extra: str = "") -> web.Response:
        """Dispatch the public GET surface."""
        if not self._coordinator.active or not self._options.get(CONF_ENABLED, True):
            # Deliberately indistinguishable from a path that was never configured.
            return web.Response(text="404: Not Found", status=404)

        if not self._limiter.allow(request.remote):
            response = web.Response(text="429: Too Many Requests", status=429)
            response.headers["Retry-After"] = "60"
            return response

        licence = self._coordinator.license_state
        if not licence.may_serve:
            return self._unavailable(
                licence.message or "This public dashboard is currently unavailable."
            )

        route = extra.strip("/")
        if route in ("", "index.html"):
            return await self._page()
        if route == "healthz":
            return self._json({"status": "ok", "path": self.public_path})
        if route == "config.json":
            return self._json(await self._coordinator.async_public_config())
        if route == "states.json":
            return self._json(await self._coordinator.async_public_states())
        if route == "stats.json":
            period = request.query.get("period", "month")
            if period not in PERIODS:
                return self._json({"error": "unknown period"}, status=400)
            return self._json(await self._coordinator.async_public_statistics(period))
        if route == "app.js":
            return self._decorate(
                web.Response(
                    text=self._coordinator.renderer_js(),
                    content_type="application/javascript",
                )
            )
        if route == "app.css":
            return self._decorate(
                web.Response(text=_asset("app.css"), content_type="text/css")
            )
        return web.Response(text="404: Not Found", status=404)

    async def _page(self) -> web.Response:
        """The HTML shell, with the sanitized config inlined to save a round trip."""
        config = await self._coordinator.async_public_config()
        title = config.get("dashboard", {}).get("title") or "Dashboard"
        html = (
            _asset("index.html")
            .replace("{{title}}", _escape(title))
            .replace("{{base}}", f"/{self.public_path}")
            .replace("{{bootstrap}}", json.dumps(config, default=str))
        )
        return self._decorate(web.Response(text=html, content_type="text/html"))


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
