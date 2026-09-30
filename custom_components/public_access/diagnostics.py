"""Diagnostics: show the owner exactly what is and is not being published."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DATA_REGISTERED_PATHS
from .const import CONF_PUBLIC_PATH, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = stored.get("coordinator")
    options = {**entry.data, **entry.options}

    report: dict[str, Any] = {"error": "not_loaded"}
    if coordinator is not None:
        report = await coordinator.async_owner_report()

    return {
        "public_path": options.get(CONF_PUBLIC_PATH),
        # A license key saved by a version before 0.5 is left out.
        "options": {k: v for k, v in options.items() if k not in ("license_key", "license_server")},
        "published": report,
        "last_visit": _last_visit(hass, options.get(CONF_PUBLIC_PATH)),
    }


def _last_visit(hass: HomeAssistant, public_path: str | None) -> dict[str, Any] | None:
    """How the latest public visitor was told apart (no addresses: privacy)."""
    registered = hass.data.get(DOMAIN, {}).get(DATA_REGISTERED_PATHS, {})
    _, view = registered.get(public_path, (None, None))
    return getattr(view, "last_visit", None)
