"""Builds the public payloads and caches them.

Public traffic must never fan out to the recorder, so every payload is cached for
the configured window. A 30-day, 7-series statistics read measured ~9 ms on the
reference instance, so this is about absorbing traffic spikes, not latency.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import assets, data as ha_data
from .const import (
    CONF_CACHE_SECONDS,
    CONF_DASHBOARD,
    CONF_EARLIEST_DATE,
    CONF_SHOW_DEVICES,
    CONF_VIEW_PATH,
    DEFAULT_CACHE_SECONDS,
)
from .license import LicenseManager, LicenseState
from .sanitize import SanitizedDashboard, sanitize_dashboard, uses_energy_cards, view_matches

_LOGGER = logging.getLogger(__name__)



class PublicDashboardCoordinator:
    """Owns the sanitized view of one dashboard plus its cached payloads."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, license: LicenseManager
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._license = license
        self._sanitized: SanitizedDashboard | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        # Cleared on unload: the route cannot be unregistered, so the view checks
        # this instead and answers 404 once the entry is gone.
        self.active = True

    # -- configuration ---------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def license_state(self) -> LicenseState:
        return self._license.state

    @property
    def _ttl(self) -> float:
        return float(self.options.get(CONF_CACHE_SECONDS, DEFAULT_CACHE_SECONDS))

    def invalidate(self) -> None:
        """Drop every cached payload, e.g. after the dashboard was edited."""
        self._sanitized = None
        self._cache.clear()

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl:
            return None
        return value

    def _store(self, key: str, value: Any) -> Any:
        self._cache[key] = (time.monotonic(), value)
        return value

    # -- the sanitized dashboard ----------------------------------------------

    async def async_sanitized(self) -> SanitizedDashboard | None:
        """The published view, sanitized. Cached until invalidated."""
        if self._sanitized is not None:
            return self._sanitized
        url_path = self.options.get(CONF_DASHBOARD)
        if not url_path:
            return None
        config = await ha_data.async_load_dashboard_config(self.hass, url_path)
        if config is None:
            _LOGGER.warning(
                "Dashboard %s has no stored configuration and cannot be published",
                url_path,
            )
            return None
        self._sanitized = sanitize_dashboard(config, self.options.get(CONF_VIEW_PATH))
        return self._sanitized

    async def async_allowed_statistic_ids(self) -> set[str]:
        """Statistic ids the public API may read: dashboard plus energy prefs."""
        sanitized = await self.async_sanitized()
        if sanitized is None:
            return set()
        ids = set(sanitized.statistic_ids)
        if uses_energy_cards(sanitized.cards):
            prefs = await ha_data.async_energy_prefs(self.hass)
            ids |= ha_data.energy_statistic_ids(prefs)
        return ids

    # -- public payloads -------------------------------------------------------

    async def async_public_config(self) -> dict[str, Any]:
        """The bootstrap payload: sanitized cards, locale, energy structure."""
        if (cached := self._cached("config")) is not None:
            return cached

        sanitized = await self.async_sanitized()
        if sanitized is None:
            return self._store(
                "config",
                {
                    "dashboard": {"title": None, "cards": []},
                    "error": "no_dashboard_config",
                    "generated_at": dt_util.utcnow().isoformat(),
                },
            )

        payload: dict[str, Any] = {
            "dashboard": sanitized.public_config(),
            "meta": ha_data.locale_info(self.hass),
            "generated_at": dt_util.utcnow().isoformat(),
        }
        if uses_energy_cards(sanitized.cards):
            payload["energy"] = await self._energy_structure()
        return self._store("config", payload)

    async def _energy_structure(self) -> dict[str, Any]:
        """Which statistic ids play which role, for the energy cards.

        Handles both the unified grid schema and the legacy flow_from/flow_to shape.
        """
        prefs = await ha_data.async_energy_prefs(self.hass)
        out: dict[str, Any] = {
            "solar": [],
            "battery": {"to": [], "from": []},
            "grid": {"import": [], "export": [], "cost": [], "compensation": []},
            "gas": [],
            "water": [],
            "devices": [],
        }
        if not prefs:
            return out

        for source in prefs.get("energy_sources") or []:
            if not isinstance(source, dict):
                continue
            kind = source.get("type")
            if kind == "solar":
                if sid := source.get("stat_energy_from"):
                    out["solar"].append(sid)
            elif kind == "battery":
                if sid := source.get("stat_energy_to"):
                    out["battery"]["to"].append(sid)
                if sid := source.get("stat_energy_from"):
                    out["battery"]["from"].append(sid)
            elif kind == "grid":
                flows_from = source.get("flow_from")
                flows_to = source.get("flow_to")
                if flows_from or flows_to:  # legacy shape
                    for flow in flows_from or []:
                        if sid := (flow or {}).get("stat_energy_from"):
                            out["grid"]["import"].append(sid)
                        if sid := (flow or {}).get("stat_cost"):
                            out["grid"]["cost"].append(sid)
                    for flow in flows_to or []:
                        if sid := (flow or {}).get("stat_energy_to"):
                            out["grid"]["export"].append(sid)
                        if sid := (flow or {}).get("stat_compensation"):
                            out["grid"]["compensation"].append(sid)
                else:  # unified shape
                    if sid := source.get("stat_energy_from"):
                        out["grid"]["import"].append(sid)
                    if sid := source.get("stat_energy_to"):
                        out["grid"]["export"].append(sid)
                    if sid := source.get("stat_cost"):
                        out["grid"]["cost"].append(sid)
                    if sid := source.get("stat_compensation"):
                        out["grid"]["compensation"].append(sid)
            elif kind in ("gas", "water"):
                if sid := source.get("stat_energy_from"):
                    out[kind].append(sid)

        if self.options.get(CONF_SHOW_DEVICES, True):
            for device in prefs.get("device_consumption") or []:
                if isinstance(device, dict) and device.get("stat_consumption"):
                    out["devices"].append(
                        {
                            "id": device["stat_consumption"],
                            "name": device.get("name"),
                        }
                    )
        return out

    async def async_public_states(self) -> dict[str, Any]:
        """Current values for the entities the published cards reference."""
        if (cached := self._cached("states")) is not None:
            return cached
        sanitized = await self.async_sanitized()
        entity_ids = set(sanitized.entity_ids) if sanitized else set()
        return self._store(
            "states",
            {
                "states": ha_data.entity_states(self.hass, entity_ids),
                "generated_at": dt_util.utcnow().isoformat(),
            },
        )

    async def async_public_statistics(self, period: str) -> dict[str, Any]:
        """Statistics for the allowlisted ids over a named period."""
        key = f"stats:{period}"
        if (cached := self._cached(key)) is not None:
            return cached

        ids = await self.async_allowed_statistic_ids()
        earliest = None
        if raw := self.options.get(CONF_EARLIEST_DATE):
            earliest = dt_util.parse_datetime(raw) or None
        series = await ha_data.async_statistics(self.hass, ids, period, earliest)
        metadata = await ha_data.async_statistics_metadata(self.hass, ids)
        return self._store(
            key,
            {
                "period": period,
                "series": series,
                "metadata": metadata,
                "generated_at": dt_util.utcnow().isoformat(),
            },
        )

    async def async_mirror_allowlists(self) -> tuple[set[str] | None, set[str] | None]:
        """What the mirrored frontend may read: every entity and statistic the
        published view refers to, plus the energy preferences' statistics.

        This scans the raw view rather than the sanitized one: in mirror mode
        the real frontend draws every card, custom ones included, so their
        entities must be readable — but nothing outside the view is.
        """
        url_path = self.options.get(CONF_DASHBOARD)
        config = await ha_data.async_load_dashboard_config(self.hass, url_path) if url_path else None
        if not config:
            return set(), set()
        view_path = self.options.get(CONF_VIEW_PATH) or None
        chosen = None
        for view in config.get("views", []) or []:
            if isinstance(view, dict) and view_matches(view, view_path):
                chosen = view
                break
        entities, statistics = _referenced_ids(chosen or {})
        prefs = await ha_data.async_energy_prefs(self.hass)
        statistics |= ha_data.energy_statistic_ids(prefs)
        # Statistics of plain sensors share the entity id.
        statistics |= entities
        return entities, statistics

    async def async_mirror_templates(self) -> set[str]:
        """Template strings the published view contains, verbatim.

        The markdown card renders its content through render_template, which
        can otherwise read any state; only text the owner put in the view may be
        rendered.
        """
        url_path = self.options.get(CONF_DASHBOARD)
        config = await ha_data.async_load_dashboard_config(self.hass, url_path) if url_path else None
        if not config:
            return set()
        view_path = self.options.get(CONF_VIEW_PATH) or None
        found: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if "{{" in value or "{%" in value or len(value) > 0:
                    found.add(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for view in config.get("views", []) or []:
            if isinstance(view, dict) and view_matches(view, view_path):
                walk(view)
                break
        return found

    # -- owner-facing ----------------------------------------------------------

    async def async_owner_report(self) -> dict[str, Any]:
        """What the sanitizer removed, for diagnostics. Never served publicly."""
        sanitized = await self.async_sanitized()
        if sanitized is None:
            return {"error": "no_dashboard_config"}
        return {
            "published_title": sanitized.title,
            "card_count": len(sanitized.cards),
            "entity_allowlist": sorted(sanitized.entity_ids),
            "statistic_allowlist": sorted(await self.async_allowed_statistic_ids()),
            "sanitizer_report": sanitized.report.as_dict(),
            "license": self.license_state.as_dict(),
            "renderer": {
                "installed": assets.installed_version(),
                "offered": self._license.payload_version,
            },
        }


_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_STATISTIC_RE = re.compile(r"^[a-z0-9_]+:[a-z0-9_]+$")


def _referenced_ids(node: Any) -> tuple[set[str], set[str]]:
    """Every entity id and external statistic id mentioned anywhere in a view.

    Card configs are free-form, especially custom cards, so this looks at every
    string value rather than known keys. Over-including a string that merely
    looks like an entity id costs nothing: it only widens the read allowlist to
    something the owner wrote into the view themselves.
    """
    entities: set[str] = set()
    statistics: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if _ENTITY_RE.match(value):
                entities.add(value)
            elif _STATISTIC_RE.match(value):
                statistics.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return entities, statistics
