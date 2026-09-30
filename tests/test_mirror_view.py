"""The mirror publishes exactly the chosen view."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.public_access.mirror import MirrorSession  # noqa: E402


def _session(view_path):
    session = MirrorSession.__new__(MirrorSession)
    session._view_path, session._dashboard = view_path, "energy-public"
    return session


CONFIG = {"views": [{"path": "overview", "cards": []}, {"path": 123456, "cards": []}]}


def test_only_the_chosen_view_is_sent():
    assert _session("123456")._one_view(CONFIG)["views"] == [{"path": 123456, "cards": []}]


def test_a_missing_view_publishes_nothing_not_the_first_one():
    """Never fall back to another view: it was not chosen and may be private."""
    assert _session("gone")._one_view(CONFIG)["views"] == []


def test_the_frontend_glue_ships_with_the_integration():
    """Since 0.5 nothing is downloaded: mirror_core is part of the package."""
    from custom_components.public_access import assets

    core = assets.mirror_core()
    assert core.API_VERSION == 1
    assert callable(core.assemble_page)
    for name in ("inbound", "outbound", "local_answer", "silent_subscription"):
        assert hasattr(core.Session, name), name
