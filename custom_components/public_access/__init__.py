"""Public Access — publish one Home Assistant dashboard publicly, read-only."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from . import data as ha_data
from .const import CONF_LICENSE_KEY, CONF_PUBLIC_PATH, DOMAIN
from .coordinator import PublicDashboardCoordinator
from .license import LicenseManager
from .view import PublicDashboardView

_LOGGER = logging.getLogger(__name__)

DATA_COORDINATOR = "coordinator"
DATA_REGISTERED_PATHS = "registered_paths"

# Home Assistant fires this when a dashboard is edited.
EVENT_LOVELACE_UPDATED = "lovelace_updated"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the domain store."""
    hass.data.setdefault(DOMAIN, {DATA_REGISTERED_PATHS: {}})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one published dashboard."""
    domain_data = hass.data.setdefault(DOMAIN, {DATA_REGISTERED_PATHS: {}})

    licence = LicenseManager(
        hass,
        entry.data.get(CONF_LICENSE_KEY, ""),
        ha_data.instance_fingerprint(hass),
    )
    await licence.async_load()
    if not licence.state.may_serve:
        _LOGGER.warning(
            "Public Access is configured but not serving: %s",
            licence.state.message or licence.state.status,
        )

    coordinator = PublicDashboardCoordinator(hass, entry, licence)
    domain_data[entry.entry_id] = {DATA_COORDINATOR: coordinator}

    public_path = {**entry.data, **entry.options}.get(CONF_PUBLIC_PATH)
    registered: dict[str, str] = domain_data[DATA_REGISTERED_PATHS]

    if public_path and public_path not in registered:
        # aiohttp cannot unregister a route, so each path is claimed once per restart.
        hass.http.register_view(PublicDashboardView(hass, public_path, coordinator))
        registered[public_path] = entry.entry_id
        _LOGGER.info(
            "Public Access is serving /%s (read-only, unauthenticated)", public_path
        )
    elif public_path and registered.get(public_path) != entry.entry_id:
        _LOGGER.error("The path /%s is already claimed by another entry", public_path)

    stale = [
        path
        for path, owner in registered.items()
        if owner == entry.entry_id and path != public_path
    ]
    if stale:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "restart_required",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="restart_required",
            translation_placeholders={
                "old": ", ".join(f"/{path}" for path in stale),
                "new": f"/{public_path}",
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "restart_required")

    @callback
    def _dashboard_changed(_event: Event) -> None:
        """Re-sanitize after the owner edits the dashboard."""
        coordinator.invalidate()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_LOVELACE_UPDATED, _dashboard_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-read options. A changed public path needs a restart to take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entry.

    The claimed route survives until Home Assistant restarts, so the coordinator is
    deactivated instead: the view then answers 404 like an unconfigured path.
    """
    domain_data = hass.data.get(DOMAIN, {})
    stored = domain_data.pop(entry.entry_id, None)
    if stored and (coordinator := stored.get(DATA_COORDINATOR)):
        coordinator.active = False
    return True
