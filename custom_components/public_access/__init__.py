"""Public Access — publish one Home Assistant dashboard publicly, read-only."""

from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from datetime import timedelta

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.loader import async_get_integration

from . import assets, data as ha_data, payload
from .const import (
    CONF_LICENSE_KEY,
    CONF_PUBLIC_PATH,
    DEFAULT_LICENSE_SERVER,
    DOMAIN,
    LICENSE_REFRESH_HOURS,
    SERVICE_REFRESH_LICENSE,
)
from .coordinator import PublicDashboardCoordinator
from .license import (
    STATUS_GRACE,
    STATUS_INVALID,
    STATUS_PAST_DUE,
    LicenseManager,
    async_forget,
)
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

    integration = await async_get_integration(hass, DOMAIN)
    fingerprint = ha_data.instance_fingerprint(hass)
    # Always the production server: an older entry may still carry the
    # placeholder the 0.1 config flow saved, and nobody needs to change it.
    server_url = DEFAULT_LICENSE_SERVER
    license = LicenseManager(
        hass,
        entry.data.get(CONF_LICENSE_KEY, ""),
        fingerprint,
        server_url,
        plugin_version=str(integration.version or ""),
    )
    await license.async_load()
    if not license.state.may_serve:
        _LOGGER.warning(
            "Public Access is configured but not serving: %s",
            license.state.message or license.state.status,
        )

    coordinator = PublicDashboardCoordinator(hass, entry, license)
    domain_data[entry.entry_id] = {DATA_COORDINATOR: coordinator}

    async def _sync_payload() -> None:
        """Fetch the licensed renderer if the server offers a newer one.

        Failure is not fatal: whatever renderer is already installed keeps
        serving, and the bundled fallback keeps the page working regardless.
        """
        wanted = license.payload_version
        if not wanted or not license.state.may_serve:
            return
        if wanted == assets.installed_version():
            return
        await payload.async_install(
            hass,
            server_url=server_url,
            license_key=entry.data.get(CONF_LICENSE_KEY, ""),
            fingerprint=fingerprint,
            version=wanted,
        )

    if assets.mirror_core() is None and license.state.may_serve:
        # Mirror mode needs the glue from a recent payload. The payload version
        # on record may predate it (an update from 0.2), so ask the server now
        # rather than at the next scheduled check, hours away.
        await license.async_refresh(force=True)
    await _sync_payload()

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

    @callback
    def _dashboard_changed(_event: Event) -> None:
        """Re-sanitize after the owner edits the dashboard."""
        coordinator.invalidate()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_LOVELACE_UPDATED, _dashboard_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    def _update_subscription_notice() -> None:
        """A repair notice for the owner while the trial or subscription is
        about to end, or has ended and the page is on the grace period. It is
        the one place the owner learns this inside Home Assistant."""
        state = license.state
        ends = state.subscription_ends_at
        days_left = None if not ends else int((ends - time.time()) // 86400)
        ending_soon = days_left is not None and days_left <= 3
        on_grace = state.status in (STATUS_GRACE, STATUS_PAST_DUE)
        if (ending_soon or on_grace) and state.status != STATUS_INVALID:
            ir.async_create_issue(
                hass,
                DOMAIN,
                "subscription_ending",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="subscription_ending",
                translation_placeholders={
                    "days": str(max(days_left or 0, 0)),
                    "status": state.status,
                    "url": state.checkout_url or "https://github.com/dllfpp/ha-public-access",
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, "subscription_ending")

    _update_subscription_notice()

    async def _refresh_license(_now) -> None:
        """Re-check the subscription. A failure here is not fatal: the cached
        entitlement carries the page through until it expires, and then through
        the grace period."""
        await license.async_refresh()
        _update_subscription_notice()
        await _sync_payload()

    entry.async_on_unload(
        async_track_time_interval(
            hass, _refresh_license, timedelta(hours=LICENSE_REFRESH_HOURS)
        )
    )

    async def _handle_refresh(_call: ServiceCall) -> None:
        """Re-check the subscription now.

        Without this, someone who has just paid, or just had a license released,
        waits up to half a day for the page to come back.
        """
        await license.async_refresh(force=True)
        _update_subscription_notice()
        await _sync_payload()
        coordinator.invalidate()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_LICENSE, _handle_refresh)
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
    """The integration was deleted: forget its cached entitlement too, so a new
    entry starts by activating its own key instead of inheriting this one."""
    await async_forget(hass)
