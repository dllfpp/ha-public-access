"""The view step offers the dashboard's views by name."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.public_access.config_flow import (  # noqa: E402
    FIRST_VIEW,
    _stored_view,
    view_options,
)


def test_views_are_offered_by_name_with_their_address():
    dashboard = {"views": [
        {"title": "Home"},                      # first, no address: reachable as first view
        {"title": "Solar", "path": "solar"},
        {"title": "Numbers", "path": 123456},   # YAML number
        {"title": "No address"},                # cannot be picked
    ]}
    options = view_options(dashboard)
    assert [o["value"] for o in options] == [FIRST_VIEW, "solar", "123456"]
    assert options[0]["label"] == "Home — first view"
    assert options[2]["label"] == "Numbers — /123456"


def test_first_view_is_stored_as_no_path():
    assert _stored_view(FIRST_VIEW) is None
    assert _stored_view("") is None
    assert _stored_view("solar") == "solar"


def test_a_path_published_by_another_entry_is_refused():
    from types import SimpleNamespace

    from custom_components.public_access import config_flow

    other = SimpleNamespace(entry_id="a", data={"public_path": "solar"}, options={})
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain: [other]))
    config_flow.ha_data.registered_panel_paths = lambda h: set()
    assert config_flow.validate_public_path(hass, "solar") == "path_published"
    assert config_flow.validate_public_path(hass, "solar", "a") is None
    assert config_flow.validate_public_path(hass, "weather") is None
