"""Public Access — publish one Home Assistant dashboard publicly, read-only."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from homeassistant.helpers.storage import Store

from . import assets
from .const import CONF_PUBLIC_PATH, DOMAIN
from .coordinator import PublicDashboardCoordinator
from .view import PublicDashboardView

_LOGGER = logging.getLogger(__name__)

DATA_COORDINATOR = "coordinator"
DATA_REGISTERED_PATHS = "registered_paths"

# Set up from the UI only; there is nothing to configure in YAML.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Home Assistant fires this when a dashboard is edited.
EVENT_LOVELACE_UPDATED = "lovelace_updated"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the domain store."""
    hass.data.setdefault(DOMAIN, {DATA_REGISTERED_PATHS: {}})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one published dashboard."""
    domain_data = hass.data.setdefault(DOMAIN, {DATA_REGISTERED_PATHS: {}})

    # Read every static file now, off the event loop: serving a request must never
    # touch the filesystem.
    await assets.async_preload(hass)

    coordinator = PublicDashboardCoordinator(hass, entry)
    domain_data[entry.entry_id] = {DATA_COORDINATOR: coordinator}

    public_path = {**entry.data, **entry.options}.get(CONF_PUBLIC_PATH)
    # path -> (entry_id, view). The view is kept so a path claimed earlier in this
    # run can be pointed at a new coordinator instead of staying dead.
    registered: dict[str, tuple[str, PublicDashboardView]] = domain_data[
        DATA_REGISTERED_PATHS
    ]

    if public_path:
        if public_path in registered:
            _, view = registered[public_path]
            view.rebind(coordinator)
            registered[public_path] = (entry.entry_id, view)
            _LOGGER.info("Public Access reattached to /%s", public_path)
        else:
            # aiohttp cannot unregister a route: a path is claimed once per restart.
            view = PublicDashboardView(hass, public_path, coordinator)
            hass.http.register_view(view)
            registered[public_path] = (entry.entry_id, view)
            _LOGGER.info(
                "Public Access is serving /%s (read-only, unauthenticated)", public_path
            )

    stale = [
        path
        for path, (owner, _view) in registered.items()
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
    # Notices from older versions that no longer apply: the proxy check
    # (0.4.3-0.4.5) and the subscription (before 0.5, when Public Access became free).
    for old_issue in ("proxy_not_trusted", "subscription_ending"):
        ir.async_delete_issue(hass, DOMAIN, old_issue)

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration was deleted: remove anything older versions left behind."""
    await _async_forget_license_files(hass)


async def _async_forget_license_files(hass: HomeAssistant) -> None:
    """The cached entitlement and downloaded payload of versions before 0.5."""
    import shutil
    from pathlib import Path

    await Store(hass, 1, "public_access.license").async_remove()
    payload_dir = Path(hass.config.path(".storage", "public_access"))
    await hass.async_add_executor_job(shutil.rmtree, payload_dir, True)
