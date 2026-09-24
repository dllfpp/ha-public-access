"""Mirror mode: the real Home Assistant frontend, behind bulletproof glass.

The visitor gets Home Assistant's own frontend — the same JavaScript, the same
cards, custom cards, animations, whatever layout the owner chose — so nothing is
re-implemented and nothing is assumed. What changes is where its websocket goes:
not to Home Assistant, but to this module, which pretends to be Home Assistant
and forwards only what a read-only viewer may do.

Two independent layers make it read-only:

1. **Allowlist.** Only the message types listed below are forwarded. Service
   calls, saves, scripts, templates and everything else are answered with an
   error and never reach Home Assistant.
2. **Permissions.** The forwarded messages run as a system user in Home
   Assistant's own `system-read-only` group, so even a message that slipped past
   the allowlist would be refused by Home Assistant itself.

No real token exists in the browser: the page seeds the frontend with a fake one,
and this endpoint accepts it because it authenticates nobody — it is public by
design, exactly like the live and snapshot modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import WSMsgType, web

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.auth.models import RefreshToken, User
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .sanitize import pin_default_panel, view_matches

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "public_access.viewer"
VIEWER_NAME = "Public Access viewer"

# Concurrent public websocket connections, in total. Each one is a live
# connection on the owner's instance, so this is a safety valve, not a target.
MAX_CONNECTIONS = 25

# What the frontend needs to draw a dashboard and nothing more. Every type not
# listed is refused. Types marked "filtered" are rewritten so the visitor only
# sees the entities and statistics the published view uses.
ALLOWED_TYPES: frozenset[str] = frozenset(
    {
        "ping",
        "supported_features",
        "auth/current_user",
        "get_config",
        "get_services",
        "get_panels",  # rewritten: only the published dashboard exists
        "get_states",  # filtered
        "subscribe_entities",  # filtered
        "subscribe_events",  # limited to harmless event types
        "unsubscribe_events",
        "frontend/get_themes",
        "frontend/get_translations",
        "frontend/get_user_data",
        "frontend/subscribe_user_data",  # the viewer's own (empty) preferences
        "frontend/subscribe_system_data",  # frontend feature flags, nothing private
        "frontend/get_icons",
        "brands/access_token",  # lets the frontend fetch integration logos
        "labs/subscribe",  # experimental-feature flags
        # Deliberately absent: repairs/list_issues and persistent_notification/*
        # would leak the owner's repair issues and notifications.
        "config/area_registry/list",
        "config/floor_registry/list",
        "config/label_registry/list",
        "config/device_registry/list",
        "config/entity_registry/list",  # filtered
        "config/entity_registry/list_for_display",  # filtered
        "lovelace/config",  # rewritten: the published dashboard, one view
        "lovelace/info",  # resource mode; the frontend asks at boot
        "lovelace/resources",  # custom cards need their JavaScript
        # render_template is allowed ONLY for a template string that appears
        # verbatim in the published view (the markdown card renders its content
        # through it). Anything else could read every state and is refused.
        "render_template",
        "energy/get_prefs",
        "energy/info",
        "energy/validate",
        "energy/solar_forecast",
        "energy/fossil_energy_consumption",
        "recorder/info",
        "recorder/list_statistic_ids",
        "recorder/get_statistics_metadata",
        "recorder/statistics_during_period",
        "recorder/statistic_during_period",
        "history/history_during_period",  # filtered
        "history/stream",  # filtered
        "weather/subscribe_forecast",
        "sensor/numeric_device_classes",
        "manifest/list",
        "manifest/get",
    }
)

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "state_changed",
        "core_config_updated",
        "themes_updated",
        "panels_updated",
        "lovelace_updated",
        "component_loaded",
        "entity_registry_updated",
        "area_registry_updated",
    }
)

# Frontend preference stores. Forwarded, but any default_panel in them is
# rewritten to the public path (see sanitize.pin_default_panel).
FRONTEND_DATA_TYPES: frozenset[str] = frozenset(
    {"frontend/get_user_data", "frontend/subscribe_user_data", "frontend/subscribe_system_data"}
)

# Answered with an empty, successful result rather than forwarded or refused.
QUIET_TYPES: dict[str, Any] = {
    "persistent_notification/subscribe": None,
    "repairs/list_issues": {"issues": []},
}

_connections = 0


async def async_get_viewer(hass: HomeAssistant) -> tuple[User, RefreshToken]:
    """The read-only system user the mirrored connection runs as.

    Created once and remembered; a refresh token is needed only because Home
    Assistant's connection object wants one for its audit context.
    """
    store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load() or {}
    user = None
    if user_id := data.get("user_id"):
        user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_active:
        user = await hass.auth.async_create_system_user(
            VIEWER_NAME, group_ids=[GROUP_ID_READ_ONLY]
        )
        await store.async_save({"user_id": user.id})
        _LOGGER.info("Created the read-only viewer user %s", user.id)
    token = next(iter(user.refresh_tokens.values()), None)
    if token is None:
        token = await hass.auth.async_create_refresh_token(
            user, client_name="Public Access mirror"
        )
    return user, token


# The page scripts and the frontend glue (the "lovelace" alias panel, silent
# subscriptions) come from the licensed renderer payload: see
# assets.mirror_core(). Everything that decides what a visitor may reach stays
# here, in the open, and runs before that glue sees a message.


class MirrorSession:
    """One visitor's websocket, proxied through a read-only viewer connection."""

    def __init__(
        self,
        hass: HomeAssistant,
        ws: web.WebSocketResponse,
        user: User,
        token: RefreshToken,
        remote: str | None,
        *,
        public_path: str,
        dashboard: str,
        view_path: str | None,
        entity_ids: set[str] | None,
        statistic_ids: set[str] | None,
        templates: set[str] | None = None,
        core: Any,
    ) -> None:
        from homeassistant.components.websocket_api.connection import ActiveConnection
        from homeassistant.components.websocket_api.messages import (
            error_message,
        )

        self._hass = hass
        self._ws = ws
        self._public_path = public_path
        self._dashboard = dashboard
        self._view_path = view_path
        self._entities = entity_ids
        self._statistics = statistic_ids
        self._templates = templates or set()
        self._types: dict[int, str] = {}
        # The licensed glue, called only after this class's own filters.
        self._core = core.Session(public_path=public_path, dashboard=dashboard)
        self._loop = hass.loop
        self._error = error_message
        self._connection = ActiveConnection(
            _LOGGER, hass, self._send, user, token, remote
        )

    # -- outbound (Home Assistant -> visitor) ---------------------------------

    def _send(self, message: bytes | str | dict[str, Any]) -> None:
        if isinstance(message, (bytes, str)):
            raw = message.decode() if isinstance(message, bytes) else message
            try:
                decoded = json.loads(raw)
            except ValueError:
                self._loop.create_task(self._ws.send_str(raw))
                return
            message = decoded
        message = self._filter_outbound(message)
        if message is not None:
            # Filters first, glue second: the glue only adapts what may be seen.
            message = self._core.outbound(self._types.get(message.get("id"), ""), message)
            self._loop.create_task(self._ws.send_str(json.dumps(message, default=str)))

    def _filter_outbound(self, message: dict[str, Any]) -> dict[str, Any] | None:
        msg_id = message.get("id")
        kind = self._types.get(msg_id, "")
        if message.get("type") == "result" and message.get("success") is False:
            # Diagnostics: an error Home Assistant itself returned to a forwarded
            # message. These surface in the page as unhandled rejections.
            _LOGGER.debug("Mirror: HA answered %s with %s", kind, message.get("error"))
        if kind in FRONTEND_DATA_TYPES:
            # Results for get_*, events for subscribe_*: both carry the data.
            pin_default_panel(message.get("result"), self._public_path)
            pin_default_panel(message.get("event"), self._public_path)
            return message
        if message.get("type") == "result" and isinstance(message.get("result"), (list, dict)):
            result = message["result"]
            if kind == "get_states":
                message["result"] = [
                    s for s in result if self._entity_allowed(s.get("entity_id"))
                ]
            elif kind in ("config/entity_registry/list",):
                message["result"] = [
                    e for e in result if self._entity_allowed(e.get("entity_id"))
                ]
            elif kind == "config/entity_registry/list_for_display" and isinstance(result, dict):
                result["entities"] = [
                    e for e in result.get("entities", []) if self._entity_allowed(e.get("ei"))
                ]
            elif kind == "get_panels" and isinstance(result, dict):
                message["result"] = self._panels(result)
            elif kind == "lovelace/config" and isinstance(result, dict):
                message["result"] = self._one_view(result)
            elif kind == "auth/current_user" and isinstance(result, dict):
                result["is_admin"] = False
                result["is_owner"] = False
        elif message.get("type") == "event" and kind == "subscribe_entities":
            event = message.get("event") or {}
            for bucket in ("a", "c", "r"):
                if isinstance(event.get(bucket), dict):
                    event[bucket] = {
                        k: v for k, v in event[bucket].items() if self._entity_allowed(k)
                    }
                elif isinstance(event.get(bucket), list):
                    event[bucket] = [k for k in event[bucket] if self._entity_allowed(k)]
        elif message.get("type") == "event" and kind == "subscribe_events":
            data = (message.get("event") or {}).get("data") or {}
            if "entity_id" in data and not self._entity_allowed(data.get("entity_id")):
                return None
        return message

    def _entity_allowed(self, entity_id: Any) -> bool:
        if self._entities is None:
            return True
        return isinstance(entity_id, str) and entity_id in self._entities

    def _panels(self, panels: dict[str, Any]) -> dict[str, Any]:
        """Only one panel exists for the visitor: the published dashboard, under
        the public path. The frontend then asks for lovelace/config with that
        url_path, which is rewritten back to the real dashboard."""
        source = panels.get(self._dashboard) or {}

        def panel(url_path: str) -> dict[str, Any]:
            return {
                "component_name": "lovelace",
                "icon": source.get("icon"),
                "title": source.get("title") or "Dashboard",
                "config": {"mode": "storage"},
                "url_path": url_path,
                "require_admin": False,
                "config_panel_domain": None,
            }

        return {self._public_path: panel(self._public_path)}

    def _one_view(self, config: dict[str, Any]) -> dict[str, Any]:
        """Publish exactly one view; the others are not even sent to the browser.

        If the configured view is gone (renamed, deleted), publish nothing rather
        than the first view: that one was never chosen and may be private."""
        views = [v for v in config.get("views", []) if isinstance(v, dict)]
        chosen = next((v for v in views if view_matches(v, self._view_path)), None)
        if chosen is None:
            _LOGGER.warning(
                "The published view %r is not in dashboard %r (views: %s); publishing nothing",
                self._view_path,
                self._dashboard,
                ", ".join(str(v.get("path")) for v in views) or "none",
            )
        return {**config, "views": [chosen] if chosen else []}

    # -- inbound (visitor -> Home Assistant) ----------------------------------

    def _reply_ok(self, msg_id: int, result: Any = None) -> None:
        self._loop.create_task(
            self._ws.send_str(
                json.dumps({"id": msg_id, "type": "result", "success": True, "result": result})
            )
        )

    def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        msg_id = msg.get("id")

        # Requests that must not be forwarded but where "nothing" is the honest
        # answer for a viewer: no notifications, no repair issues, and the
        # frontend's own error reports are simply dropped. Answering these with
        # an error instead leaves unhandled rejections in the frontend.
        if isinstance(msg_id, int) and (
            kind in QUIET_TYPES
            or (kind == "call_service" and msg.get("domain") == "system_log")
        ):
            result = QUIET_TYPES.get(kind)
            self._loop.create_task(
                self._ws.send_str(
                    json.dumps({"id": msg_id, "type": "result", "success": True, "result": result})
                )
            )
            return

        if not isinstance(msg_id, int) or kind not in ALLOWED_TYPES:
            self._loop.create_task(
                self._ws.send_str(
                    json.dumps(self._error(msg_id or 0, "unauthorized", "Read-only public view"))
                )
            )
            _LOGGER.debug(
                "Mirror refused %s %s",
                kind,
                f"{msg.get('domain')}.{msg.get('service')}" if kind == "call_service" else "",
            )
            return

        answered, result = self._core.local_answer(kind, msg)
        if answered:
            self._reply_ok(msg_id, result)
            return

        if kind == "subscribe_events":
            if msg.get("event_type", "*") not in ALLOWED_EVENT_TYPES:
                # Never forwarded. How the frontend is told (an error, or a
                # subscription that never fires) is the glue's business.
                self._reply_ok(msg_id, self._core.silent_subscription(msg_id))
                return
        elif kind == "subscribe_entities" and self._entities is not None:
            msg = {**msg, "entity_ids": sorted(self._entities)}
        elif kind in ("history/history_during_period", "history/stream"):
            wanted = msg.get("entity_ids") or []
            msg = {**msg, "entity_ids": [e for e in wanted if self._entity_allowed(e)]}
            if not msg["entity_ids"]:
                self._loop.create_task(self._ws.send_str(json.dumps({"id": msg_id, "type": "result", "success": True, "result": {}})))
                return
        elif kind == "recorder/statistics_during_period" and self._statistics is not None:
            wanted = msg.get("statistic_ids") or []
            msg = {**msg, "statistic_ids": [s for s in wanted if s in self._statistics]}
        elif kind == "lovelace/config":
            msg = {**msg, "url_path": self._dashboard}
        elif kind == "render_template":
            if msg.get("template") not in self._templates:
                self._loop.create_task(
                    self._ws.send_str(json.dumps(self._error(msg_id, "unauthorized", "Read-only public view")))
                )
                _LOGGER.debug("Mirror refused render_template (not from the published view)")
                return

        self._types[msg_id] = kind
        self._connection.async_handle(self._core.inbound(kind, msg))

    def close(self) -> None:
        self._connection.async_handle_close()


async def async_serve_websocket(
    hass: HomeAssistant,
    request: web.Request,
    *,
    public_path: str,
    dashboard: str,
    view_path: str | None,
    entity_ids: set[str] | None,
    statistic_ids: set[str] | None,
    templates: set[str] | None = None,
) -> web.WebSocketResponse:
    """The public websocket endpoint."""
    global _connections  # noqa: PLW0603

    ws = web.WebSocketResponse(heartbeat=55)
    await ws.prepare(request)

    if _connections >= MAX_CONNECTIONS:
        await ws.send_str(json.dumps({"type": "auth_invalid", "message": "Too many viewers"}))
        await ws.close()
        return ws

    from homeassistant.const import __version__

    await ws.send_str(json.dumps({"type": "auth_required", "ha_version": __version__}))
    first = await ws.receive()
    if first.type != WSMsgType.TEXT or json.loads(first.data).get("type") != "auth":
        await ws.close()
        return ws
    await ws.send_str(json.dumps({"type": "auth_ok", "ha_version": __version__}))

    from . import assets

    core = assets.mirror_core()
    if core is None:
        await ws.send_str(json.dumps({"type": "auth_invalid", "message": "Mirror mode is not available"}))
        await ws.close()
        return ws

    user, token = await async_get_viewer(hass)
    session = MirrorSession(
        hass, ws, user, token, request.remote,
        public_path=public_path, dashboard=dashboard, view_path=view_path,
        entity_ids=entity_ids, statistic_ids=statistic_ids, templates=templates,
        core=core,
    )
    _connections += 1
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            try:
                data = json.loads(message.data)
            except ValueError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    session.handle(item)
    finally:
        _connections -= 1
        session.close()
    return ws


async def async_render_page(hass: HomeAssistant, public_path: str) -> str | None:
    """Home Assistant's own index page, with the licensed page scripts put in
    place. None when the licensed mirror module is not installed."""
    from homeassistant.components import frontend

    from . import assets

    core = assets.mirror_core()
    if core is None:
        return None

    view = None
    for resource in hass.http.app.router.resources():
        if isinstance(resource, frontend.IndexView):
            view = resource
            break
    if view is None:
        raise RuntimeError("frontend index is not available")

    template = view._template_cache or await hass.async_add_executor_job(view.get_template)  # noqa: SLF001
    html = template.render(
        theme_color=frontend.MANIFEST_JSON["theme_color"],
        extra_modules=hass.data[frontend.DATA_EXTRA_MODULE_URL].urls,
        extra_js_es5=hass.data[frontend.DATA_EXTRA_JS_URL_ES5].urls,
    )
    return core.assemble_page(html, public_path)
