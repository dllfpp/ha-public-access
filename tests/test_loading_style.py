"""The launch screen can be painted in the owner's colour, and nothing else."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).parents[1] / "custom_components" / "public_access" / "mirror_core.py"
_SPEC = importlib.util.spec_from_file_location("mirror_core", _PATH)
core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(core)

INDEX = "<html><head><style>html{background-color:#fafafa}</style></head><body></body></html>"


def test_empty_or_invalid_colour_adds_nothing():
    for value in (None, "", "black", "#12", "#1234567", "#zzzzzz", "red;}</style><script>x</script>"):
        assert core.loading_style(value) == "", value


def test_dark_colour_paints_the_launch_screen_and_switches_the_attribution():
    css = core.loading_style("#1b1c1e")
    assert "background-color:#1b1c1e" in css
    assert "open-home-foundation-on-dark.svg" in css
    assert core.loading_style("#111") == core.loading_style("#111").strip()
    assert "open-home-foundation-on-dark.svg" in core.loading_style("#111")


def test_light_colour_keeps_the_light_attribution():
    css = core.loading_style("#ffffff")
    assert "background-color:#ffffff" in css
    assert "on-dark" not in css


def test_style_comes_before_home_assistants_own_styles():
    page = core.assemble_page(INDEX, "public_solar", "#1b1c1e")
    assert page.index("background-color:#1b1c1e") < page.index("background-color:#fafafa")


def test_page_unchanged_without_a_colour():
    assert core.assemble_page(INDEX, "public_solar") == core.assemble_page(INDEX, "public_solar", "")
    assert "html:root" not in core.assemble_page(INDEX, "public_solar")
