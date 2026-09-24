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
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
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
    CONF_MODE,
    CONF_NOINDEX,
    CONF_SNAPSHOT_TTL,
    CONF_VIEW_PATH,
    DEFAULT_CACHE_SECONDS,
    DEFAULT_SNAPSHOT_TTL,
    MODE_LIVE,
    MODE_MIRROR,
    MODE_SNAPSHOT,
    PERIODS,
    RATE_LIMIT_PER_MINUTE,
    SNAPSHOT_DIR,
    SNAPSHOT_IMAGE,
    SNAPSHOT_REQUEST,
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
        body = json.dumps(payload, default=str)
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

        licence = self._coordinator.license_state
        if not licence.may_serve:
            return self._unavailable(
                licence.message or "This public dashboard is currently unavailable."
            )

        route = extra.strip("/")
        mode = self._options.get(CONF_MODE, MODE_LIVE)
        if mode == MODE_MIRROR:
            return await self._mirror(request, route)
        if mode == MODE_SNAPSHOT:
            # In snapshot mode the data endpoints are switched off entirely: the
            # page is a photograph, and the less surface the better.
            return await self._snapshot(route)
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
                    text=assets.renderer_js(),
                    content_type="application/javascript",
                )
            )
        if route == "app.css":
            return self._decorate(
                web.Response(text=assets.get("app.css"), content_type="text/css")
            )
        return web.Response(text="404: Not Found", status=404)

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
            return self._json({"status": "ok", "path": self.public_path, "mode": MODE_MIRROR})
        html = await mirror.async_render_page(self.hass, self.public_path)
        response = web.Response(text=html, content_type="text/html")
        # The frontend loads scripts from the same origin and inlines none of
        # ours except the bootstrap, so the policy stays tight but must allow
        # websockets and the frontend's own assets.
        response.headers["Cache-Control"] = "no-store"
        if options.get(CONF_NOINDEX, True):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    async def _snapshot(self, route: str) -> web.Response:
        """Serve the photograph taken by the companion container.

        The image is read off the event loop. When it is older than the configured
        refresh, a request file is dropped for the companion, which renders on
        demand — so a page nobody opens never costs a capture. That touch is the
        only thing this integration ever writes, and it is to its own directory.
        """
        directory = Path(self.hass.config.path(SNAPSHOT_DIR))
        image = directory / SNAPSHOT_IMAGE
        ttl = int(self._options.get(CONF_SNAPSHOT_TTL, DEFAULT_SNAPSHOT_TTL))
        want_bytes = route == "snapshot.png"

        def _read() -> tuple[bytes, float, tuple[int, int]]:
            try:
                stat = image.stat()
            except OSError:
                return b"", 0.0, (0, 0)
            if time.time() - stat.st_mtime > ttl:
                try:
                    (directory / SNAPSHOT_REQUEST).touch()
                except OSError:
                    _LOGGER.debug("Could not request a new snapshot", exc_info=True)
            with image.open("rb") as handle:
                head = handle.read(24)
                size = (0, 0)
                if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
                    size = struct.unpack(">II", head[16:24])
                data = head + handle.read() if want_bytes else b""
            return data, stat.st_mtime, size

        data, mtime, (width, height) = await self.hass.async_add_executor_job(_read)

        if route == "healthz":
            return self._json(
                {"status": "ok", "path": self.public_path, "mode": MODE_SNAPSHOT, "snapshot_at": mtime or None}
            )
        if not mtime:
            return self._unavailable(
                "The first snapshot has not been taken yet. Check that the "
                "Public Access Snapshot add-on is running."
            )
        if want_bytes:
            response = web.Response(body=data, content_type="image/png")
            response.headers["ETag"] = f'"{int(mtime)}"'
            return self._decorate(response)
        if route in ("", "index.html"):
            updated = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            title = self._options.get("title") or "Dashboard"
            html = (
                assets.get("snapshot.html")
                .replace("{{title}}", _escape(str(title)))
                .replace("{{base}}", f"/{self.public_path}")
                .replace("{{version}}", str(int(mtime)))
                .replace("{{width}}", str(width or 1280))
                .replace("{{height}}", str(height or 800))
                .replace("{{updated}}", updated)
            )
            return self._decorate(web.Response(text=html, content_type="text/html"))
        return web.Response(text="404: Not Found", status=404)

    async def _page(self) -> web.Response:
        """The HTML shell, with the sanitized config inlined to save a round trip."""
        config = await self._coordinator.async_public_config()
        title = config.get("dashboard", {}).get("title") or "Dashboard"
        html = (
            assets.get("index.html")
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
