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
