"""Diagnostics: show the owner exactly what is and is not being published."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_LICENSE_KEY, CONF_PUBLIC_PATH, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = stored.get("coordinator")
    options = {**entry.data, **entry.options}
    key = options.get(CONF_LICENSE_KEY, "")

    report: dict[str, Any] = {"error": "not_loaded"}
    if coordinator is not None:
        report = await coordinator.async_owner_report()

    return {
        "public_path": options.get(CONF_PUBLIC_PATH),
        "license_key": f"{key[:4]}…" if key else None,
        "options": {
            key: value
            for key, value in options.items()
            if key != CONF_LICENSE_KEY
        },
        "published": report,
    }
