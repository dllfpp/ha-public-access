"""Downloading and installing the licensed renderer payload.

The payload is a signed archive served by the license server. Its signature is
verified against the same pinned public key used for entitlements **before the
archive is opened**, so neither a compromised proxy nor a tampered mirror can put
code on a customer's public page.

If anything goes wrong the plugin keeps whatever renderer it already has, falling
back to the one bundled with the integration. A failed download must never take a
working page down.
"""

from __future__ import annotations

import io
import logging
import tarfile
from pathlib import Path

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import assets
from .license import ISSUER_PUBLIC_KEY_B64, _b64url_decode

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 120
MAX_PAYLOAD_BYTES = 12 * 1024 * 1024
ENTRY_NAME = "app.js"


def verify_signature(blob: bytes, signature_b64: str) -> bool:
    """Ed25519 signature over the whole archive, against the pinned key."""
    if not ISSUER_PUBLIC_KEY_B64:
        _LOGGER.error("No issuer public key is pinned; refusing the payload")
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(_b64url_decode(ISSUER_PUBLIC_KEY_B64))
        key.verify(_b64url_decode(signature_b64), blob)
    except InvalidSignature:
        _LOGGER.error("The renderer payload's signature is invalid")
        return False
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Could not verify the renderer payload")
        return False
    return True


def _extract(blob: bytes) -> str | None:
    """Pull app.js out of the archive, refusing anything else.

    Only one known member is read, by exact name, so a crafted archive cannot
    write outside the cache directory or hand us something unexpected.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            member = archive.getmember(ENTRY_NAME)
            if not member.isfile() or member.size > MAX_PAYLOAD_BYTES:
                _LOGGER.error("Payload member %s is not a plain file", ENTRY_NAME)
                return None
            handle = archive.extractfile(member)
            if handle is None:
                return None
            return handle.read().decode("utf-8")
    except (tarfile.TarError, KeyError, UnicodeDecodeError, OSError):
        _LOGGER.exception("The renderer payload could not be read")
        return None


def _write_cache(path: Path, text: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    (path.parent / "version.txt").write_text(version, encoding="utf-8")


async def async_install(
    hass: HomeAssistant,
    *,
    server_url: str,
    license_key: str,
    fingerprint: str,
    version: str,
) -> bool:
    """Download, verify and install one payload version. True when installed."""
    session = async_get_clientsession(hass)
    url = f"{server_url.rstrip('/')}/v1/payload/{version}"
    params = {"license_key": license_key, "fingerprint": fingerprint}

    try:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
        ) as response:
            response.raise_for_status()
            signature = response.headers.get("X-Payload-Signature", "")
            declared = response.content_length
            if declared is not None and declared > MAX_PAYLOAD_BYTES:
                _LOGGER.error(
                    "The renderer payload is larger than expected (%d bytes); "
                    "refusing it",
                    declared,
                )
                return False
            # Read the whole body: a partial read would be verified against a
            # signature over the complete archive and always fail.
            blob = await response.read()
    except Exception as error:  # noqa: BLE001 - keep the current renderer
        _LOGGER.warning("Could not download the renderer payload: %s", error)
        return False

    if len(blob) > MAX_PAYLOAD_BYTES:
        _LOGGER.error("The renderer payload is larger than expected; refusing it")
        return False
    if not signature:
        _LOGGER.error(
            "The license server served a renderer payload without a signature; "
            "refusing it"
        )
        return False
    if not verify_signature(blob, signature):
        return False

    text = await hass.async_add_executor_job(_extract, blob)
    if not text:
        return False

    cache = Path(hass.config.path(".storage", "public_access", "payload", ENTRY_NAME))
    await hass.async_add_executor_job(_write_cache, cache, text, version)
    assets.set_payload(text, version)
    _LOGGER.info("Installed renderer payload %s (%d bytes)", version, len(text))
    return True
