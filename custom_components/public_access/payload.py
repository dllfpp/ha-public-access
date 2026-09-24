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
# Mirror mode's frontend glue, licensed like the renderer and covered by the
# same signature. Optional in the archive, so older payloads still install.
MIRROR_CORE_NAME = "mirror_core.py"


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


def _read_member(archive: tarfile.TarFile, name: str) -> str | None:
    try:
        member = archive.getmember(name)
    except KeyError:
        return None
    if not member.isfile() or member.size > MAX_PAYLOAD_BYTES:
        _LOGGER.error("Payload member %s is not a plain file", name)
        return None
    handle = archive.extractfile(member)
    return handle.read().decode("utf-8") if handle is not None else None


def _extract(blob: bytes) -> dict[str, str] | None:
    """Pull the known members out of the archive, refusing anything else.

    Members are read by exact name, so a crafted archive cannot write outside
    the cache directory or hand us something unexpected. app.js is required;
    the mirror glue is optional.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            out = {name: text for name in (ENTRY_NAME, MIRROR_CORE_NAME)
                   if (text := _read_member(archive, name))}
    except (tarfile.TarError, UnicodeDecodeError, OSError):
        _LOGGER.exception("The renderer payload could not be read")
        return None
    if ENTRY_NAME not in out:
        _LOGGER.error("The renderer payload has no %s", ENTRY_NAME)
        return None
    return out


def _write_cache(directory: Path, files: dict[str, str], version: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in (ENTRY_NAME, MIRROR_CORE_NAME):
        path = directory / name
        if name in files:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(files[name], encoding="utf-8")
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)  # a payload without it must not keep an old one
    (directory / "version.txt").write_text(version, encoding="utf-8")


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

    files = await hass.async_add_executor_job(_extract, blob)
    if not files:
        return False

    directory = Path(hass.config.path(".storage", "public_access", "payload"))
    await hass.async_add_executor_job(_write_cache, directory, files, version)
    assets.set_payload(files[ENTRY_NAME], version)
    await assets.async_load_mirror_core(hass)
    _LOGGER.info(
        "Installed renderer payload %s (%s)", version, ", ".join(sorted(files))
    )
    return True
