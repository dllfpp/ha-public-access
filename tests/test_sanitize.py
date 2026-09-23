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


def test_external_statistics_are_separated_from_entities():
    config = {
        "views": [
            {
                "path": "pv",
                "cards": [
                    {
                        "type": "statistics-graph",
                        "entities": ["sensor.local", "external:solar"],
                    }
                ],
            }
        ]
    }
    result = sanitize.sanitize_dashboard(config, "pv")
    assert result.entity_ids == {"sensor.local"}
    assert result.statistic_ids == {"external:solar"}


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
