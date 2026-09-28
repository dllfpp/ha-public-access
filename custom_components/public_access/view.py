"""The public, unauthenticated HTTP surface.

Design rules enforced here:

* Only GET is implemented, so every other verb gets an automatic 405.
* The client never names an entity, a statistic or a date range. It may only pick a
  period from a closed enum; ids come from the sanitized dashboard and the energy
  preferences, resolved server-side.
* Nothing is served unless the integration is enabled and the license may serve.
* `X-Frame-Options: SAMEORIGIN` is applied by Home Assistant's own middleware after
  this handler returns and cannot be overridden from here, so embedding the page in
  another site requires a header rewrite at the reverse proxy. CSP, X-Robots-Tag and
  Cache-Control do pass through and are set below.
"""

from __future__ import annotations

import hashlib
from homeassistant.helpers.json import json_dumps
import logging
import time
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from . import assets
from .const import (
    CONF_CACHE_SECONDS,
    CONF_DASHBOARD,
    CONF_ENABLED,
    CONF_FRAME_ANCESTORS,
    CONF_NOINDEX,
    CONF_VIEW_PATH,
    DEFAULT_CACHE_SECONDS,
    RATE_LIMIT_PER_MINUTE,
)
from .coordinator import PublicDashboardCoordinator

_LOGGER = logging.getLogger(__name__)


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

    def rebind(self, coordinator: PublicDashboardCoordinator) -> None:
        """Point this route at a new config entry's coordinator.

        aiohttp cannot unregister a route, so when the integration is removed and
        added again without a restart the original view object is still the one
        serving this path. Without rebinding, the path would answer 404 forever
        even though the integration is configured — and removing and re-adding is
        the first thing anyone tries when something looks wrong.
        """
        self._coordinator = coordinator

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
        # Home Assistant's own encoder: State objects become their dict form, as
        # the frontend and cards expect from the real history endpoint.
        body = json_dumps(payload)
        response = web.Response(
            body=body.encode(), content_type="application/json", status=status
        )
        response.headers["ETag"] = hashlib.sha256(body.encode()).hexdigest()[:32]
        return self._decorate(response)

    def _unavailable(self, message: str) -> web.Response:
        html = assets.get("unavailable.html").replace("{{message}}", message)
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

        license = self._coordinator.license_state
        if not license.may_serve:
            return self._unavailable(
                license.message or "This public dashboard is currently unavailable."
            )

        # Mirror is the only way a dashboard is published. The live renderer and
        # the snapshot companion were removed in 0.4: entries still set to them
        # are served as mirror.
        return await self._mirror(request, extra.strip("/"))

    async def _mirror(self, request: web.Request, route: str) -> web.StreamResponse:
        """Home Assistant's real frontend over a read-only websocket proxy.

        Routes: the page itself (any sub-path, since the frontend routes views
        client-side) and `ws`, the websocket the page is steered to.
        """
        from . import mirror

        options = self._options
        dashboard = options.get(CONF_DASHBOARD) or ""
        view_path = options.get(CONF_VIEW_PATH) or None

        if route == "ws":
            entity_ids, statistic_ids = await self._coordinator.async_mirror_allowlists()
            templates = await self._coordinator.async_mirror_templates()
            return await mirror.async_serve_websocket(
                self.hass,
                request,
                public_path=self.public_path,
                dashboard=dashboard,
                view_path=view_path,
                entity_ids=entity_ids,
                statistic_ids=statistic_ids,
                templates=templates,
            )
        if route == "healthz":
            return self._json({"status": "ok", "path": self.public_path, "mode": "mirror"})
        if route == "api/history/period" or route.startswith("api/history/period/"):
            # Cards that read history over REST instead of the websocket
            # (ApexCharts) are steered here by the page; same shape, same
            # answer, but only the published view's entities come back.
            from . import history

            entity_ids, _ = await self._coordinator.async_mirror_allowlists()
            start = route.removeprefix("api/history/period").strip("/") or None
            parsed = history.parse_query(start, dict(request.query), entity_ids or set())
            if isinstance(parsed, str):
                return self._json({"message": parsed}, status=400)
            return self._json(await history.async_fetch(self.hass, parsed))
        html = await mirror.async_render_page(self.hass, self.public_path)
        if html is None:
            # The mirror's frontend glue arrives with the licensed renderer
            # payload; until it is installed there is nothing to serve.
            return self._unavailable(
                "This dashboard is being prepared. Please try again in a few minutes."
            )
        response = web.Response(text=html, content_type="text/html")
        # The frontend loads scripts from the same origin and inlines none of
        # ours except the bootstrap, so the policy stays tight but must allow
        # websockets and the frontend's own assets.
        response.headers["Cache-Control"] = "no-store"
        if options.get(CONF_NOINDEX, True):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

