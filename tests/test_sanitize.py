"""The two checks the mirror takes from sanitize.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "public_access"))

import sanitize  # noqa: E402


def test_a_numeric_view_path_still_matches():
    """`path: 123456` in YAML arrives as an int; the option is text."""
    assert sanitize.view_matches({"path": 123456}, "123456")
    assert sanitize.view_matches({"path": "solar"}, "solar")
    assert not sanitize.view_matches({"path": "solar"}, "costs")


def test_a_view_without_an_address_matches_only_when_none_was_chosen():
    assert not sanitize.view_matches({"title": "no path"}, "123456")
    assert sanitize.view_matches({"title": "no path"}, None)


def test_the_owner_default_panel_never_reaches_the_visitor():
    """HA 2026.8+: a system default dashboard made the mirror hang on "Loading"."""
    event = {"value": {"default_panel": "dashboard-tablet"}}
    assert sanitize.pin_default_panel(event, "public") == {"value": {"default_panel": "public"}}
    nested = {"core": {"default_panel": "dashboard-tablet"}, "sidebar": {"panelOrder": ["x"]}}
    sanitize.pin_default_panel(nested, "public")
    assert nested["core"]["default_panel"] == "public"
    assert nested["sidebar"] == {"panelOrder": ["x"]}
    assert sanitize.pin_default_panel(None, "public") is None
