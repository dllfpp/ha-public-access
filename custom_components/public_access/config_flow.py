"""Config and options flow.

The public path check here is a safety control, not cosmetics. A registered aiohttp
view outranks the frontend's catch-all resource, so a path that collides with a
panel or a dashboard would shadow part of the owner's own Home Assistant. Two rules
keep that impossible:

* the path must contain no hyphen — Home Assistant requires every storage dashboard
  `url_path` to contain one, so a hyphen-free path can never collide with a dashboard;
* the path must not be a reserved path or a currently registered panel.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.loader import async_get_integration

from . import data as ha_data
from .license import STATUS_OFFLINE, LicenseManager
from .const import (
    CONF_CACHE_SECONDS,
    CONF_DASHBOARD,
    CONF_ENABLED,
    CONF_FRAME_ANCESTORS,
    CONF_LICENSE_KEY,
    CONF_MODE,
    CONF_SNAPSHOT_TTL,
    CONF_NOINDEX,
    CONF_PUBLIC_PATH,
    CONF_SHOW_DEVICES,
    CONF_VIEW_PATH,
    DEFAULT_CACHE_SECONDS,
    DEFAULT_LICENSE_SERVER,
    DEFAULT_MODE,
    DEFAULT_NOINDEX,
    DEFAULT_PUBLIC_PATH,
    DEFAULT_SNAPSHOT_TTL,
    MODE_LIVE,
    MODE_MIRROR,
    MODE_SNAPSHOT,
    DOMAIN,
    RESERVED_PATHS,
    TRIAL_URL,
)

PATH_PATTERN = re.compile(r"^[a-z0-9_]{2,48}$")

# Dropdown value for a first view that has no address of its own.
FIRST_VIEW = "__first__"


def view_options(dashboard: dict[str, Any]) -> list[selector.SelectOptionDict]:
    """The dashboard's views as dropdown choices, by name.

    A view is found by its address (the last part of its URL). A view without
    one can only be reached as the first view; any other view without an
    address cannot be picked until it is given one in its settings.
    """
    options: list[selector.SelectOptionDict] = []
    for index, view in enumerate(dashboard.get("views") or []):
        path = view.get("path")
        title = view.get("title") or f"View {index + 1}"
        if path is None or str(path) == "":
            if index == 0:
                options.append(selector.SelectOptionDict(value=FIRST_VIEW, label=f"{title} — first view"))
            continue
        options.append(selector.SelectOptionDict(value=str(path), label=f"{title} — /{path}"))
    return options


def _stored_view(value: str | None) -> str | None:
    return None if not value or value == FIRST_VIEW else value


def public_base_url(hass: Any) -> str:
    """This Home Assistant's address as the internet sees it, for the example
    shown in the setup form; a placeholder when none is configured."""
    try:
        from homeassistant.helpers.network import get_url

        return get_url(hass, prefer_external=True, allow_internal=True).rstrip("/")
    except Exception:  # noqa: BLE001 - NoURLAvailableError and friends
        return "https://your-home-assistant"


def validate_public_path(hass: Any, path: str) -> str | None:
    """Return an error key, or None when the path is safe to claim."""
    if not PATH_PATTERN.match(path or ""):
        return "invalid_path"
    if path in RESERVED_PATHS:
        return "path_reserved"
    if path in ha_data.registered_panel_paths(hass):
        return "path_in_use"
    return None


class PublicAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the owner through license, dashboard and public path."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._dashboards: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the license key."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        reason = ""
        if user_input is not None:
            key = user_input[CONF_LICENSE_KEY].strip()
            # Activate now, so a refused key (a second trial on this instance,
            # a revoked or expired key) is reported here, before the owner
            # configures everything else. The saved entitlement is the one
            # setup then starts from.
            integration = await async_get_integration(self.hass, DOMAIN)
            manager = LicenseManager(
                self.hass,
                key,
                ha_data.instance_fingerprint(self.hass),
                DEFAULT_LICENSE_SERVER,
                plugin_version=str(integration.version or ""),
            )
            state = await manager.async_refresh(force=True)
            if state.may_serve:
                self._data[CONF_LICENSE_KEY] = key
                return await self.async_step_dashboard()
            if state.status == STATUS_OFFLINE:
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "license_refused"
                reason = state.message or "This key cannot be used."

        schema: dict[Any, Any] = {vol.Required(CONF_LICENSE_KEY): str}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"trial_url": TRIAL_URL, "reason": reason},
        )

    async def async_step_dashboard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which dashboard, and which view of it, to publish."""
        self._dashboards = await ha_data.async_list_publishable_dashboards(self.hass)
        if not self._dashboards:
            return self.async_abort(reason="no_dashboards")

        if user_input is not None:
            self._data[CONF_DASHBOARD] = user_input[CONF_DASHBOARD]
            views = view_options(self._dashboard(user_input[CONF_DASHBOARD]))
            if len(views) <= 1:
                # One view (or none with an address): nothing to choose.
                self._data[CONF_VIEW_PATH] = _stored_view(views[0]["value"]) if views else None
                return await self.async_step_path()
            return await self.async_step_view()

        options = [
            selector.SelectOptionDict(
                value=item["url_path"], label=f"{item['title']} ({item['url_path']})"
            )
            for item in self._dashboards
        ]
        return self.async_show_form(
            step_id="dashboard",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DASHBOARD): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )

    def _dashboard(self, url_path: str) -> dict[str, Any]:
        return next((d for d in self._dashboards if d["url_path"] == url_path), {"views": []})

    async def async_step_view(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the one view of the dashboard that will be public, by name."""
        dashboard = self._dashboard(self._data[CONF_DASHBOARD])
        if user_input is not None:
            self._data[CONF_VIEW_PATH] = _stored_view(user_input[CONF_VIEW_PATH])
            return await self.async_step_path()

        views = view_options(dashboard)
        return self.async_show_form(
            step_id="view",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VIEW_PATH, default=views[0]["value"]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=views, mode=selector.SelectSelectorMode.LIST
                        )
                    ),
                }
            ),
            description_placeholders={"dashboard": dashboard.get("title", "")},
        )

    async def async_step_path(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the public path, validated against panels and dashboards."""
        errors: dict[str, str] = {}
        if user_input is not None:
            path = user_input[CONF_PUBLIC_PATH].strip().strip("/").lower()
            error = validate_public_path(self.hass, path)
            if error:
                errors[CONF_PUBLIC_PATH] = error
            else:
                self._data[CONF_PUBLIC_PATH] = path
                return self.async_create_entry(
                    title=f"/{path}",
                    data=self._data,
                    options={
                        CONF_ENABLED: True,
                        CONF_MODE: DEFAULT_MODE,
                        CONF_NOINDEX: DEFAULT_NOINDEX,
                        CONF_CACHE_SECONDS: DEFAULT_CACHE_SECONDS,
                        CONF_SHOW_DEVICES: True,
                    },
                )

        return self.async_show_form(
            step_id="path",
            data_schema=vol.Schema(
                {vol.Required(CONF_PUBLIC_PATH, default=DEFAULT_PUBLIC_PATH): str}
            ),
            errors=errors,
            description_placeholders={"base_url": public_base_url(self.hass)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return PublicAccessOptionsFlow()


class PublicAccessOptionsFlow(OptionsFlow):
    """Change what is published and how."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            path = user_input[CONF_PUBLIC_PATH].strip().strip("/").lower()
            if path != current.get(CONF_PUBLIC_PATH):
                error = validate_public_path(self.hass, path)
                if error:
                    errors[CONF_PUBLIC_PATH] = error
            if not errors:
                return self.async_create_entry(
                    data={
                        **user_input,
                        CONF_PUBLIC_PATH: path,
                        CONF_VIEW_PATH: _stored_view((user_input.get(CONF_VIEW_PATH) or "").strip()),
                    }
                )

        dashboards = await ha_data.async_list_publishable_dashboards(self.hass)
        current_dashboard = next(
            (d for d in dashboards if d["url_path"] == current.get(CONF_DASHBOARD)), {"views": []}
        )
        views = view_options(current_dashboard)
        current_view = current.get(CONF_VIEW_PATH) or (FIRST_VIEW if views and views[0]["value"] == FIRST_VIEW else "")
        if current_view and current_view not in {v["value"] for v in views}:
            views.append(selector.SelectOptionDict(value=current_view, label=f"/{current_view} (not found)"))
        options = [
            selector.SelectOptionDict(
                value=item["url_path"], label=f"{item['title']} ({item['url_path']})"
            )
            for item in dashboards
        ] or [
            selector.SelectOptionDict(
                value=current.get(CONF_DASHBOARD, ""), label=current.get(CONF_DASHBOARD, "")
            )
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_ENABLED, default=current.get(CONF_ENABLED, True)): bool,
                vol.Required(
                    CONF_MODE, default=current.get(CONF_MODE, DEFAULT_MODE)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=MODE_LIVE, label="Live (sanitized)"),
                            selector.SelectOptionDict(
                                value=MODE_SNAPSHOT, label="Snapshot (photograph, unfiltered)"
                            ),
                            selector.SelectOptionDict(
                                value=MODE_MIRROR, label="Mirror (real frontend, read-only)"
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SNAPSHOT_TTL,
                    default=current.get(CONF_SNAPSHOT_TTL, DEFAULT_SNAPSHOT_TTL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=60, max=86400, step=30, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_DASHBOARD, default=current.get(CONF_DASHBOARD)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_VIEW_PATH, default=current_view): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=views,
                        custom_value=True,  # another dashboard's view can be typed
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_PUBLIC_PATH, default=current.get(CONF_PUBLIC_PATH)
                ): str,
                vol.Required(
                    CONF_NOINDEX, default=current.get(CONF_NOINDEX, DEFAULT_NOINDEX)
                ): bool,
                vol.Required(
                    CONF_SHOW_DEVICES, default=current.get(CONF_SHOW_DEVICES, True)
                ): bool,
                vol.Required(
                    CONF_CACHE_SECONDS,
                    default=current.get(CONF_CACHE_SECONDS, DEFAULT_CACHE_SECONDS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=10, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_FRAME_ANCESTORS,
                    default=current.get(CONF_FRAME_ANCESTORS) or "",
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
