"""The only module that touches Home Assistant internals.

Every call here is a read. There is deliberately no code path in this package that
calls a service, writes a config, or mutates state — a test asserts that.

The API shapes used here were verified against Home Assistant 2026.9.3.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import PERIODS

_LOGGER = logging.getLogger(__name__)

# State attributes that may be published. Everything else is withheld: attribute
# dicts routinely carry latitude/longitude, entity pictures and access tokens.
SAFE_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "friendly_name",
        "unit_of_measurement",
        "device_class",
        "state_class",
        "icon",
        "min",
        "max",
        "step",
    }
)


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


def period_start(period_name: str) -> Any:
    """The start of the named period, the way Home Assistant's energy dashboard
    counts it: calendar periods in the instance's timezone, not rolling windows.

    "month" is the first of this month, not the last thirty days — otherwise
    the live page and a photograph of the real dashboard would show different
    totals for what claims to be the same thing.
    """
    today = dt_util.start_of_local_day()
    if period_name == "day":
        return today
    if period_name == "week":
        return today - timedelta(days=today.weekday())
    if period_name == "month":
        return today.replace(day=1)
    if period_name == "year":
        return today.replace(month=1, day=1)
    return today


async def async_statistics(
    hass: HomeAssistant,
    statistic_ids: set[str],
    period_name: str,
    earliest: Any | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read statistics for an allowlisted set of ids over a named period.

    `period_name` comes from a closed enum; the caller never passes a raw range.
    """
    if not statistic_ids or period_name not in PERIODS:
        return {}
    recorder_period, _ = PERIODS[period_name]
    start = period_start(period_name)
    if earliest is not None and earliest > start:
        start = earliest
    # Statistics are cumulative sums, and the page draws each bucket as the
    # difference from the previous one. Fetching from one bucket *before* the
    # period gives the first bucket its baseline; without it the first day of the
    # month simply vanishes from the total. Home Assistant's frontend does the
    # same.
    if recorder_period == "hour":
        start -= timedelta(hours=1)
    elif recorder_period == "day":
        start -= timedelta(days=1)
    else:
        start = (start - timedelta(days=1)).replace(day=1)

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
    except Exception:  # noqa: BLE001 - recorder disabled
        _LOGGER.debug("Recorder unavailable", exc_info=True)
        return {}

    instance = get_instance(hass)
    result = await instance.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        set(statistic_ids),
        recorder_period,
        None,
        {"sum", "state", "mean", "min", "max"},
    )
    # Drop anything the caller did not ask for, belt and braces.
    return {
        key: [_clean_point(point) for point in points]
        for key, points in result.items()
        if key in statistic_ids
    }


def _clean_point(point: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("start", "end", "sum", "state", "mean", "min", "max"):
        if key in point and point[key] is not None:
            value = point[key]
            # Timestamps come back as epoch floats; keep them numeric.
            out[key] = value
    return out


async def async_statistics_metadata(
    hass: HomeAssistant, statistic_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Units and display names for allowlisted statistic ids."""
    if not statistic_ids:
        return {}
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import list_statistic_ids
    except Exception:  # noqa: BLE001
        return {}

    instance = get_instance(hass)
    rows = await instance.async_add_executor_job(list_statistic_ids, hass)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = row.get("statistic_id")
        if sid not in statistic_ids:
            continue
        out[sid] = {
            "name": row.get("name"),
            "unit": row.get("display_unit_of_measurement")
            or row.get("statistics_unit_of_measurement"),
            "has_sum": row.get("has_sum"),
            "has_mean": row.get("has_mean"),
        }
    return out


def entity_states(hass: HomeAssistant, entity_ids: set[str]) -> list[dict[str, Any]]:
    """Current values for allowlisted entities, with attributes filtered."""
    out: list[dict[str, Any]] = []
    for entity_id in sorted(entity_ids):
        state = hass.states.get(entity_id)
        if state is None:
            continue
        out.append(
            {
                "entity_id": entity_id,
                "state": state.state,
                "attributes": {
                    key: value
                    for key, value in state.attributes.items()
                    if key in SAFE_ATTRIBUTES
                },
                "last_changed": state.last_changed.isoformat(),
            }
        )
    return out


def instance_fingerprint(hass: HomeAssistant) -> str:
    """Stable, non-identifying instance id used for license binding."""
    import hashlib

    raw = str(hass.data.get("core.uuid") or "unknown")
    return hashlib.sha256(f"public_access:{raw}".encode()).hexdigest()[:32]


def locale_info(hass: HomeAssistant) -> dict[str, Any]:
    """Units, currency and timezone so the public page formats numbers correctly."""
    config = hass.config
    return {
        "currency": config.currency,
        "country": config.country,
        "language": config.language,
        "time_zone": str(config.time_zone),
        "unit_system": config.units.__class__.__name__,
    }
