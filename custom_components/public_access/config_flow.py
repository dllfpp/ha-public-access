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

from . import data as ha_data
from .const import (
    CONF_CACHE_SECONDS,
    CONF_DASHBOARD,
    CONF_ENABLED,
    CONF_FRAME_ANCESTORS,
    CONF_LICENSE_KEY,
    CONF_LICENSE_SERVER,
    CONF_NOINDEX,
    CONF_PUBLIC_PATH,
    CONF_SHOW_DEVICES,
    CONF_VIEW_PATH,
    DEFAULT_CACHE_SECONDS,
    DEFAULT_LICENSE_SERVER,
    DEFAULT_NOINDEX,
    DEFAULT_PUBLIC_PATH,
    DOMAIN,
    RESERVED_PATHS,
)

PATH_PATTERN = re.compile(r"^[a-z0-9_]{2,48}$")


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
    """Walk the owner through licence, dashboard and public path."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._dashboards: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the licence key."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            self._data[CONF_LICENSE_KEY] = user_input[CONF_LICENSE_KEY].strip()
            self._data[CONF_LICENSE_SERVER] = (
                user_input.get(CONF_LICENSE_SERVER) or DEFAULT_LICENSE_SERVER
            ).strip().rstrip("/")
            return await self.async_step_dashboard()

        schema: dict[Any, Any] = {vol.Required(CONF_LICENSE_KEY): str}
        if self.show_advanced_options:
            # Only useful for development and self-hosted licence servers.
            schema[vol.Optional(CONF_LICENSE_SERVER, default=DEFAULT_LICENSE_SERVER)] = str
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            description_placeholders={"docs": "https://github.com/dllfpp/ha-public-access"},
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
            view_path = (user_input.get(CONF_VIEW_PATH) or "").strip()
            self._data[CONF_VIEW_PATH] = view_path or None
            return await self.async_step_path()

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
                    vol.Optional(CONF_VIEW_PATH, default=""): str,
                }
            ),
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
                return self.async_create_entry(data={**user_input, CONF_PUBLIC_PATH: path})

        dashboards = await ha_data.async_list_publishable_dashboards(self.hass)
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
                    CONF_DASHBOARD, default=current.get(CONF_DASHBOARD)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(
                    CONF_VIEW_PATH, default=current.get(CONF_VIEW_PATH) or ""
                ): str,
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
