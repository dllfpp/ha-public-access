"""The only module that touches Home Assistant internals.

Every call here is a read. There is deliberately no code path in this package that
calls a service, writes a config, or mutates state — a test asserts that.

The API shapes used here were verified against Home Assistant 2026.9.3.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)

def registered_panel_paths(hass: HomeAssistant) -> set[str]:
    """Every path the frontend already owns, including storage dashboards.

    A registered aiohttp view outranks the frontend's catch-all resource, so a
    public path colliding with one of these would shadow the owner's own UI.
    """
    try:
        from homeassistant.components import frontend

        return set(hass.data.get(frontend.DATA_PANELS, {}) or {})
    except Exception:  # noqa: BLE001 - never block setup on introspection
        _LOGGER.debug("Could not read the panel list", exc_info=True)
        return set()


def _dashboards(hass: HomeAssistant) -> dict[str | None, Any]:
    from homeassistant.components import lovelace

    data = hass.data.get(lovelace.DOMAIN)
    return getattr(data, "dashboards", None) or {}


async def async_list_publishable_dashboards(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Dashboards that actually have a stored config.

    An auto-generated dashboard raises ConfigNotFound, so it cannot be published;
    the config flow offers only what is listed here.
    """
    out: list[dict[str, Any]] = []
    for url_path, store in _dashboards(hass).items():
        if url_path is None:
            # The default dashboard has no stable public URL of its own and is
            # usually strategy-generated; skip it rather than confuse the user.
            continue
        try:
            config = await store.async_load(False)
        except Exception:  # noqa: BLE001 - ConfigNotFound and friends
            continue
        if not isinstance(config, dict):
            continue
        views = [v for v in config.get("views", []) if isinstance(v, dict)]
        out.append(
            {
                "url_path": url_path,
                "title": config.get("title") or url_path,
                "views": [
                    {"path": v.get("path"), "title": v.get("title")} for v in views
                ],
            }
        )
    return sorted(out, key=lambda item: item["url_path"])


async def async_load_dashboard_config(
    hass: HomeAssistant, url_path: str
) -> dict[str, Any] | None:
    """Load one dashboard's stored config, or None when it has none."""
    store = _dashboards(hass).get(url_path)
    if store is None:
        return None
    try:
        config = await store.async_load(False)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Dashboard %s has no stored config", url_path, exc_info=True)
        return None
    return config if isinstance(config, dict) else None


async def async_energy_prefs(hass: HomeAssistant) -> dict[str, Any] | None:
    """The energy preferences, read through the energy manager."""
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
        return manager.data
    except Exception:  # noqa: BLE001 - energy may not be set up at all
        _LOGGER.debug("Energy preferences unavailable", exc_info=True)
        return None


def energy_statistic_ids(prefs: dict[str, Any] | None) -> set[str]:
    """Statistic ids referenced by the energy preferences.

    Handles both the unified grid schema (flat stat_energy_from/stat_energy_to)
    and the legacy flow_from/flow_to shape, since stored preferences on an
    existing install may not have been migrated yet.
    """
    ids: set[str] = set()
    if not prefs:
        return ids

    def add(value: Any) -> None:
        if isinstance(value, str) and value:
            ids.add(value)

    for source in prefs.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        for key in (
            "stat_energy_from",
            "stat_energy_to",
            "stat_cost",
            "stat_compensation",
            "stat_rate",
        ):
            add(source.get(key))
        # Legacy grid shape.
        for flow in (source.get("flow_from") or []) + (source.get("flow_to") or []):
            if isinstance(flow, dict):
                for key in (
                    "stat_energy_from",
                    "stat_energy_to",
                    "stat_cost",
                    "stat_compensation",
                ):
                    add(flow.get(key))
    for device in prefs.get("device_consumption") or []:
        if isinstance(device, dict):
            add(device.get("stat_consumption"))
    for device in prefs.get("device_consumption_water") or []:
        if isinstance(device, dict):
            add(device.get("stat_consumption"))
    return ids

