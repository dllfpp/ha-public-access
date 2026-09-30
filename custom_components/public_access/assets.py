"""Static assets, read off the event loop.

Home Assistant runs everything on one event loop, so reading a file while serving a
request stalls the whole instance. These files never change at runtime, so they are
read once during setup and served from memory afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType

from homeassistant.core import HomeAssistant

from . import mirror_core as _mirror_core

_LOGGER = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"
BUNDLED = ("unavailable.html",)

_CACHE: dict[str, str] = {}


def _read_bundled() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in BUNDLED:
        try:
            out[name] = (ASSETS_DIR / name).read_text(encoding="utf-8")
        except OSError:
            _LOGGER.error("Bundled asset %s is missing", name)
            out[name] = ""
    return out


async def async_preload(hass: HomeAssistant) -> None:
    """Read the bundled assets."""
    _CACHE.update(await hass.async_add_executor_job(_read_bundled))


def get(name: str) -> str:
    """A bundled asset, from memory."""
    return _CACHE.get(name, "")


def mirror_core() -> ModuleType:
    """Mirror mode's frontend glue (mirror_core.py, shipped with the integration)."""
    return _mirror_core
