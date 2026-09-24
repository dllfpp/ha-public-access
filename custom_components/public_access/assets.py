"""Static assets, read off the event loop.

Home Assistant runs everything on one event loop, so reading a file while serving a
request stalls the whole instance. These files never change at runtime, so they are
read once during setup and served from memory afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"
BUNDLED = ("index.html", "unavailable.html", "app.js", "app.css")

_CACHE: dict[str, str] = {}
# The renderer payload, when a licensed one has been downloaded.
_PAYLOAD_JS: str | None = None


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
    """Read the bundled assets and any cached renderer payload."""
    global _PAYLOAD_JS  # noqa: PLW0603 - module-level cache by design

    _CACHE.update(await hass.async_add_executor_job(_read_bundled))

    payload_path = Path(hass.config.path(".storage", "public_access", "payload", "app.js"))

    def _read_payload() -> str | None:
        try:
            text = payload_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return text or None

    _PAYLOAD_JS = await hass.async_add_executor_job(_read_payload)
    if _PAYLOAD_JS:
        _LOGGER.debug("Serving the licensed renderer payload")


def get(name: str) -> str:
    """A bundled asset, from memory."""
    return _CACHE.get(name, "")


def renderer_js() -> str:
    """The renderer to serve: the licensed payload if present, else the fallback."""
    return _PAYLOAD_JS or _CACHE.get("app.js", "")


def set_payload(js: str | None) -> None:
    """Install a freshly downloaded renderer payload."""
    global _PAYLOAD_JS  # noqa: PLW0603
    _PAYLOAD_JS = js or None
