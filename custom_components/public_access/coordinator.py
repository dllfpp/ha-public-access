"""What the mirror may read for one published dashboard.

It holds nothing between visits: every public connection gets the allowlists
read fresh from the published view.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import data as ha_data
from .const import (
    CONF_DASHBOARD,
    CONF_VIEW_PATH,
)
from .sanitize import view_matches

_LOGGER = logging.getLogger(__name__)



class PublicDashboardCoordinator:
    """One published dashboard: its options, and the allowlists the mirror
    applies to every visitor."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # Cleared on unload: the route cannot be unregistered, so the view checks
        # this instead and answers 404 once the entry is gone.
        self.active = True

    # -- configuration ---------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    def invalidate(self) -> None:
        """Called when the dashboard or the options change. Nothing is cached:
        the mirror's allowlists are read fresh for every visitor's connection."""

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
        """What a visitor can reach, for diagnostics. Never served publicly."""
        entities, statistics = await self.async_mirror_allowlists()
        templates = await self.async_mirror_templates()
        return {
            "dashboard": self.options.get(CONF_DASHBOARD),
            "view": self.options.get(CONF_VIEW_PATH) or "(first view)",
            "entity_allowlist": sorted(entities) if entities is not None else "(no view found)",
            "statistic_allowlist": sorted(statistics) if statistics is not None else "(no view found)",
            "templates_allowed": len(templates),
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
