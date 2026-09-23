"""Turn a Lovelace dashboard config into something safe to serve publicly.

This module is the security boundary of the product and is deliberately kept in
the open repository so it can be audited. It is a whitelist: a card type that is
not listed is never rendered, and a key that is not listed for a card type is
never emitted. Nothing here reads or writes Home Assistant state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Keys allowed on every supported card.
COMMON_KEYS: frozenset[str] = frozenset({"type", "title"})

# Supported card types mapped to the extra keys each one may carry.
CARD_KEYS: dict[str, frozenset[str]] = {
    # Energy cards take their data from the energy preferences, not from the
    # card config, so they carry almost nothing.
    "energy-date-selection": frozenset(),
    "energy-usage-graph": frozenset(),
    "energy-solar-graph": frozenset(),
    "energy-gas-graph": frozenset(),
    "energy-water-graph": frozenset(),
    "energy-distribution": frozenset(),
    "energy-sources-table": frozenset(),
    "energy-devices-graph": frozenset(),
    "energy-devices-detail-graph": frozenset(),
    "energy-self-consumption-gauge": frozenset(),
    "energy-grid-neutrality-gauge": frozenset(),
    "energy-carbon-consumed-gauge": frozenset(),
    # Generic cards.
    "statistics-graph": frozenset(
        {"entities", "stat_types", "period", "days_to_show", "chart_type", "unit"}
    ),
    "history-graph": frozenset({"entities", "hours_to_show"}),
    "statistic": frozenset({"entity", "name", "stat_type", "period", "unit"}),
    "gauge": frozenset({"entity", "min", "max", "needle", "severity", "unit", "name"}),
    "tile": frozenset({"entity", "name", "state_content", "hide_state", "vertical"}),
    "sensor": frozenset({"entity", "name", "graph", "unit", "hours_to_show"}),
    "entities": frozenset({"entities", "state_color", "show_header_toggle"}),
    "glance": frozenset(
        {"entities", "columns", "show_name", "show_state", "show_icon", "state_color"}
    ),
    # `badges` is deliberately not allowed: a badge can carry its own action.
    "heading": frozenset({"heading", "heading_style", "icon"}),
    "markdown": frozenset({"content"}),
    "grid": frozenset({"cards", "columns", "square"}),
    "vertical-stack": frozenset({"cards"}),
    "horizontal-stack": frozenset({"cards"}),
}

# Card types that are dropped outright rather than placeholdered: they can embed
# arbitrary remote content, leak local paths, or expose a control surface.
FORBIDDEN_CARDS: frozenset[str] = frozenset(
    {
        "iframe",
        "webpage",
        "picture",
        "picture-elements",
        "picture-entity",
        "picture-glance",
        "map",
        "media-control",
        "alarm-panel",
        "thermostat",
        "humidifier",
        "light",
        "button",
        "entity-button",
        "conditional",
        "shopping-list",
        "todo-list",
        "calendar",
        "energy-date-selection-legacy",
    }
)

# Anything that could trigger an action must never survive.
ACTION_KEYS: frozenset[str] = frozenset(
    {
        "tap_action",
        "hold_action",
        "double_tap_action",
        "confirmation",
        "service",
        "service_data",
        "target",
        "action",
        "url",
        "url_path",
        "path",
        "navigation_path",
        "webhook",
        "camera_image",
        "camera_view",
        "image",
        "elements",
        "badges",
    }
)

PLACEHOLDER_TYPE = "public-access-unsupported"


@dataclass
class SanitizeReport:
    """What the sanitizer removed. Surfaced to the owner, never to the public."""

    forbidden: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    actions_stripped: list[str] = field(default_factory=list)
    keys_stripped: list[str] = field(default_factory=list)
    views_dropped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        """Return only the non-empty buckets."""
        return {
            key: sorted(set(value))
            for key, value in {
                "forbidden": self.forbidden,
                "unsupported": self.unsupported,
                "actions_stripped": self.actions_stripped,
                "keys_stripped": self.keys_stripped,
                "views_dropped": self.views_dropped,
            }.items()
            if value
        }


@dataclass
class SanitizedDashboard:
    """The publishable result."""

    title: str | None
    cards: list[dict[str, Any]]
    entity_ids: set[str]
    statistic_ids: set[str]
    report: SanitizeReport

    def public_config(self) -> dict[str, Any]:
        """The payload handed to the browser. Contains no report."""
        return {"title": self.title, "cards": self.cards}


def _sanitize_card(card: dict[str, Any], report: SanitizeReport) -> dict[str, Any] | None:
    """Whitelist one card. Returns None when the card must not be published."""
    card_type = card.get("type")
    if not isinstance(card_type, str):
        return None
    if card_type in FORBIDDEN_CARDS:
        report.forbidden.append(card_type)
        return None
    if card_type not in CARD_KEYS:
        report.unsupported.append(card_type)
        # A placeholder keeps the layout honest without leaking the card's config.
        return {"type": PLACEHOLDER_TYPE, "original_type": card_type}

    allowed = COMMON_KEYS | CARD_KEYS[card_type]
    clean: dict[str, Any] = {}
    for key, value in card.items():
        if key in ACTION_KEYS:
            report.actions_stripped.append(f"{card_type}.{key}")
            continue
        if key not in allowed:
            report.keys_stripped.append(f"{card_type}.{key}")
            continue
        if key == "cards" and isinstance(value, list):
            nested = [
                _sanitize_card(item, report) for item in value if isinstance(item, dict)
            ]
            clean[key] = [item for item in nested if item is not None]
        elif key == "entities" and isinstance(value, list):
            clean[key] = _clean_entities(value, report, card_type)
        else:
            clean[key] = value
    return clean


def _clean_entities(
    entities: list[Any], report: SanitizeReport, card_type: str
) -> list[dict[str, Any] | str]:
    """Normalise an entities list, dropping per-row actions and extra keys."""
    out: list[dict[str, Any] | str] = []
    for item in entities:
        if isinstance(item, str):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key, value in item.items():
            if key in ACTION_KEYS:
                report.actions_stripped.append(f"{card_type}.entities.{key}")
                continue
            if key not in {"entity", "name", "icon", "unit", "stat_type"}:
                report.keys_stripped.append(f"{card_type}.entities.{key}")
                continue
            row[key] = value
        if row.get("entity"):
            out.append(row)
    return out


# Cards whose data comes from long-term statistics rather than current state. For
# these, a plain entity id is also a statistic id, so it must reach the statistic
# allowlist or the card would have nothing to draw.
STATISTIC_CARDS: frozenset[str] = frozenset({"statistics-graph", "statistic"})


def _collect_ids(card: dict[str, Any], entities: set[str], statistics: set[str]) -> None:
    """Gather the ids a sanitized card legitimately needs."""
    from_statistics = card.get("type") in STATISTIC_CARDS
    for key in ("entity", "entities"):
        value = card.get(key)
        items = [value] if isinstance(value, str) else (value or [])
        if not isinstance(items, list):
            continue
        for item in items:
            candidate = item.get("entity") if isinstance(item, dict) else item
            if not isinstance(candidate, str):
                continue
            if from_statistics or ":" in candidate:
                # An id containing ':' is an external statistic, never an entity.
                statistics.add(candidate)
            else:
                entities.add(candidate)
    for nested in card.get("cards") or []:
        if isinstance(nested, dict):
            _collect_ids(nested, entities, statistics)


def sanitize_dashboard(
    config: dict[str, Any], view_path: str | None = None
) -> SanitizedDashboard:
    """Reduce a full dashboard config to a single publishable view.

    Exactly one view is published. Every other view is discarded, so a dashboard
    may safely contain private views alongside the public one.
    """
    report = SanitizeReport()
    views = config.get("views") if isinstance(config, dict) else None
    if not isinstance(views, list):
        views = []

    chosen: dict[str, Any] | None = None
    for view in views:
        if not isinstance(view, dict):
            continue
        matches = view_path is None or view.get("path") == view_path
        if chosen is None and matches:
            chosen = view
        else:
            report.views_dropped.append(str(view.get("path") or view.get("title") or "?"))

    raw_cards = (chosen or {}).get("cards")
    if not isinstance(raw_cards, list):
        raw_cards = []
    # Sections-layout dashboards keep cards inside sections instead.
    for section in (chosen or {}).get("sections") or []:
        if isinstance(section, dict) and isinstance(section.get("cards"), list):
            raw_cards = [*raw_cards, *section["cards"]]

    cards: list[dict[str, Any]] = []
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        clean = _sanitize_card(item, report)
        if clean is not None:
            cards.append(clean)

    entity_ids: set[str] = set()
    statistic_ids: set[str] = set()
    for card in cards:
        _collect_ids(card, entity_ids, statistic_ids)

    title = (chosen or {}).get("title")
    return SanitizedDashboard(
        title=title if isinstance(title, str) else None,
        cards=cards,
        entity_ids=entity_ids,
        statistic_ids=statistic_ids,
        report=report,
    )


def uses_energy_cards(cards: list[dict[str, Any]]) -> bool:
    """True when any published card draws on the energy preferences."""
    for card in cards:
        if str(card.get("type", "")).startswith("energy-"):
            return True
        if uses_energy_cards(card.get("cards") or []):
            return True
    return False
