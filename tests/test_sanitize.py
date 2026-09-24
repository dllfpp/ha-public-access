"""Sanitizer guarantees. Runs without a Home Assistant install."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "public_access"))

import sanitize  # noqa: E402

HOSTILE = {
    "views": [
        {
            "title": "Public",
            "path": "pv",
            "cards": [
                {"type": "energy-usage-graph", "title": "Usage"},
                {"type": "markdown", "content": "hello"},
                {"type": "gauge", "entity": "sensor.pv", "tap_action": {"action": "toggle"}},
                {"type": "iframe", "url": "https://evil.example/secret"},
                {
                    "type": "picture-elements",
                    "image": "/local/plan.png",
                    "elements": [{"type": "state-badge", "entity": "lock.front"}],
                },
                {"type": "entities", "entities": ["sensor.a", {"entity": "sensor.b", "secret": 1}]},
                {"type": "some-custom-card", "entity": "sensor.hidden"},
                {
                    "type": "vertical-stack",
                    "cards": [
                        {"type": "iframe", "url": "https://evil.example"},
                        {"type": "tile", "entity": "sensor.c", "tap_action": {"action": "more-info"}},
                    ],
                },
            ],
        },
        {"title": "Private", "path": "private", "cards": [{"type": "markdown", "content": "SECRET"}]},
    ]
}


def published_types(result):
    types = []

    def walk(cards):
        for card in cards:
            types.append(card["type"])
            walk(card.get("cards") or [])

    walk(result.cards)
    return types


def test_forbidden_cards_are_dropped_everywhere():
    result = sanitize.sanitize_dashboard(HOSTILE, "pv")
    types = published_types(result)
    assert "iframe" not in types
    assert "picture-elements" not in types
    # including inside a nested stack
    assert types.count(sanitize.PLACEHOLDER_TYPE) == 1


def test_unknown_card_becomes_a_placeholder_without_its_config():
    result = sanitize.sanitize_dashboard(HOSTILE, "pv")
    placeholder = next(c for c in result.cards if c["type"] == sanitize.PLACEHOLDER_TYPE)
    assert placeholder == {"type": sanitize.PLACEHOLDER_TYPE, "original_type": "some-custom-card"}
    # the unknown card's entity must not reach the allowlist
    assert "sensor.hidden" not in result.entity_ids


def test_actions_and_unknown_keys_never_survive():
    result = sanitize.sanitize_dashboard(HOSTILE, "pv")
    serialized = str(result.cards)
    for needle in ("tap_action", "hold_action", "service", "url", "elements", "secret"):
        assert needle not in serialized


def test_only_the_chosen_view_is_published():
    result = sanitize.sanitize_dashboard(HOSTILE, "pv")
    assert result.title == "Public"
    assert "SECRET" not in str(result.cards)
    assert "private" in result.report.views_dropped


def test_allowlist_contains_only_published_entities():
    result = sanitize.sanitize_dashboard(HOSTILE, "pv")
    assert result.entity_ids == {"sensor.pv", "sensor.a", "sensor.b", "sensor.c"}
    assert "lock.front" not in result.entity_ids


def test_statistics_cards_put_their_ids_on_the_statistic_allowlist():
    """For a statistics card a plain entity id is also its statistic id."""
    config = {
        "views": [
            {
                "path": "v",
                "cards": [
                    {"type": "statistics-graph", "entities": ["sensor.local", "external:solar"]},
                    {"type": "statistic", "entity": "sensor.counter"},
                ],
            }
        ]
    }
    result = sanitize.sanitize_dashboard(config, "v")
    assert result.statistic_ids == {"sensor.local", "external:solar", "sensor.counter"}
    assert result.entity_ids == set()


def test_external_statistic_ids_never_land_on_the_entity_allowlist():
    config = {
        "views": [
            {"path": "v", "cards": [{"type": "entities", "entities": ["sensor.a", "external:x"]}]}
        ]
    }
    result = sanitize.sanitize_dashboard(config, "v")
    assert result.entity_ids == {"sensor.a"}
    assert result.statistic_ids == {"external:x"}


def test_badges_are_stripped_from_a_heading_card():
    config = {
        "views": [
            {
                "path": "v",
                "cards": [
                    {
                        "type": "heading",
                        "heading": "Overview",
                        "badges": [
                            {"entity": "lock.front", "tap_action": {"action": "toggle"}}
                        ],
                    }
                ],
            }
        ]
    }
    result = sanitize.sanitize_dashboard(config, "v")
    assert result.cards == [{"type": "heading", "heading": "Overview"}]
    assert "lock.front" not in result.entity_ids


def test_glance_entities_are_published_and_cleaned():
    config = {
        "views": [
            {
                "path": "v",
                "cards": [
                    {
                        "type": "glance",
                        "columns": 3,
                        "entities": [
                            "sensor.a",
                            {"entity": "sensor.b", "name": "B", "tap_action": {"action": "toggle"}},
                        ],
                    }
                ],
            }
        ]
    }
    result = sanitize.sanitize_dashboard(config, "v")
    assert result.cards[0]["columns"] == 3
    assert result.entity_ids == {"sensor.a", "sensor.b"}
    assert "tap_action" not in str(result.cards)


def test_sections_layout_cards_are_published():
    config = {
        "views": [
            {
                "path": "pv",
                "sections": [{"type": "grid", "cards": [{"type": "markdown", "content": "hi"}]}],
            }
        ]
    }
    result = sanitize.sanitize_dashboard(config, "pv")
    assert [card["type"] for card in result.cards] == ["markdown"]


def test_empty_or_broken_config_is_safe():
    for config in ({}, {"views": None}, {"views": [{"cards": "nonsense"}]}):
        result = sanitize.sanitize_dashboard(config, None)
        assert result.cards == []
        assert result.entity_ids == set()


def test_a_numeric_view_path_still_matches():
    """`path: 123456` in YAML arrives as an int; the option is text."""
    config = {
        "views": [
            {"path": "default", "cards": [{"type": "markdown", "content": "private"}]},
            {"path": 123456, "cards": [{"type": "markdown", "content": "public"}]},
        ]
    }
    result = sanitize.sanitize_dashboard(config, "123456")
    assert [card["content"] for card in result.cards] == ["public"]
    assert sanitize.view_matches({"path": 123456}, "123456")
    assert not sanitize.view_matches({"title": "no path"}, "123456")
    assert sanitize.view_matches({"title": "no path"}, None)


def test_a_missing_view_publishes_nothing():
    """Never fall back to another view: it was not chosen and may be private."""
    config = {"views": [{"path": "default", "cards": [{"type": "markdown", "content": "private"}]}]}
    result = sanitize.sanitize_dashboard(config, "gone")
    assert result.cards == []


def test_the_owner_default_panel_never_reaches_the_visitor():
    """HA 2026.8+: a system default dashboard made the mirror hang on "Loading"."""
    event = {"value": {"default_panel": "dashboard-tablet"}}
    assert sanitize.pin_default_panel(event, "public") == {"value": {"default_panel": "public"}}
    nested = {"core": {"default_panel": "dashboard-tablet"}, "sidebar": {"panelOrder": ["x"]}}
    sanitize.pin_default_panel(nested, "public")
    assert nested["core"]["default_panel"] == "public"
    assert nested["sidebar"] == {"panelOrder": ["x"]}
    assert sanitize.pin_default_panel(None, "public") is None
