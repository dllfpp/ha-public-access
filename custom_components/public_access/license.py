"""Subscription validation.

The entitlement is an Ed25519-signed blob issued by the licence server and verified
here **offline** against a pinned public key, so a licence-server outage can never
take down a customer's public page: a cached entitlement keeps working until it
expires, and then for a grace period on top.

The signature verification below is real. What is still stubbed (milestone M3) is
the network call that obtains the entitlement — see `async_refresh`.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "public_access.license"

# Pinned issuer key. Replaced with the production key when the licence server is
# deployed (M3); an empty value means "unsigned development mode".
ISSUER_PUBLIC_KEY_B64: str = ""

ENTITLEMENT_PREFIX = "PA1"
GRACE_SECONDS = 14 * 24 * 3600

STATUS_ACTIVE = "active"
STATUS_TRIALING = "trialing"
STATUS_GRACE = "grace"
STATUS_EXPIRED = "expired"
STATUS_INVALID = "invalid"
STATUS_UNLICENSED = "unlicensed"

SERVING_STATUSES = frozenset({STATUS_ACTIVE, STATUS_TRIALING, STATUS_GRACE})


@dataclass
class LicenseState:
    """Everything the rest of the integration needs to know about the licence."""

    status: str = STATUS_UNLICENSED
    plan: str | None = None
    expires_at: float | None = None
    features: set[str] = field(default_factory=set)
    message: str | None = None

    @property
    def may_serve(self) -> bool:
        """Whether the public route should serve content."""
        return self.status in SERVING_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "plan": self.plan,
            "expires_at": self.expires_at,
            "features": sorted(self.features),
            "message": self.message,
        }


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_entitlement(token: str, public_key_b64: str) -> dict[str, Any] | None:
    """Verify a signed entitlement offline. Returns its payload, or None."""
    try:
        prefix, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        _LOGGER.warning("Entitlement is malformed")
        return None
    if prefix != ENTITLEMENT_PREFIX:
        _LOGGER.warning("Unknown entitlement version %s", prefix)
        return None

    payload_raw = _b64url_decode(payload_b64)
    if public_key_b64:
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            key = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_key_b64))
            key.verify(_b64url_decode(signature_b64), payload_raw)
        except InvalidSignature:
            _LOGGER.error("Entitlement signature is invalid")
            return None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not verify the entitlement signature")
            return None

    try:
        payload = json.loads(payload_raw)
    except ValueError:
        _LOGGER.warning("Entitlement payload is not JSON")
        return None
    return payload if isinstance(payload, dict) else None


def state_from_payload(payload: dict[str, Any], fingerprint: str) -> LicenseState:
    """Turn a verified entitlement payload into a state, honouring the grace period."""
    if payload.get("inst") not in (None, fingerprint):
        return LicenseState(
            status=STATUS_INVALID,
            message="This licence is bound to a different Home Assistant instance.",
        )

    expires_at = float(payload.get("exp") or 0)
    plan = payload.get("plan")
    features = set(payload.get("features") or [])
    now = time.time()
    status = str(payload.get("status") or STATUS_ACTIVE)

    if expires_at and now > expires_at + GRACE_SECONDS:
        return LicenseState(
            status=STATUS_EXPIRED,
            plan=plan,
            expires_at=expires_at,
            message="The subscription has expired.",
        )
    if expires_at and now > expires_at:
        return LicenseState(
            status=STATUS_GRACE,
            plan=plan,
            expires_at=expires_at,
            features=features,
            message="Could not reach the licence server; serving on the grace period.",
        )
    if status not in SERVING_STATUSES:
        return LicenseState(
            status=STATUS_INVALID, plan=plan, expires_at=expires_at, message="Subscription inactive."
        )
    return LicenseState(status=status, plan=plan, expires_at=expires_at, features=features)


class LicenseManager:
    """Holds the current licence state and knows how to refresh it."""

    def __init__(self, hass: HomeAssistant, license_key: str, fingerprint: str) -> None:
        self._hass = hass
        self._key = license_key
        self._fingerprint = fingerprint
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.state = LicenseState()

    async def async_load(self) -> LicenseState:
        """Restore a cached entitlement, then try to refresh it."""
        cached = await self._store.async_load() or {}
        token = cached.get("entitlement")
        if isinstance(token, str):
            payload = verify_entitlement(token, ISSUER_PUBLIC_KEY_B64)
            if payload:
                self.state = state_from_payload(payload, self._fingerprint)
        await self.async_refresh()
        return self.state

    async def async_refresh(self) -> LicenseState:
        """Obtain a fresh entitlement from the licence server.

        M3: POST the licence key and instance fingerprint to /v1/activate (first
        run) or /v1/heartbeat (subsequently), store the returned entitlement, and
        keep the cached one on any network error so the grace period applies.

        Until the licence server exists, a key prefixed `DEV-` unlocks the plugin
        locally so the rest of the integration can be developed and tested.
        """
        if self._key.startswith("DEV-"):
            self.state = LicenseState(
                status=STATUS_ACTIVE,
                plan="development",
                expires_at=None,
                features={"energy", "generic-cards"},
                message="Development licence: no licence server contacted.",
            )
            return self.state

        if not self._key:
            self.state = LicenseState(
                status=STATUS_UNLICENSED, message="No licence key configured."
            )
            return self.state

        if self.state.status == STATUS_UNLICENSED:
            self.state = LicenseState(
                status=STATUS_INVALID,
                message=(
                    "Licence validation is not available yet in this build. "
                    "Use a DEV- key for local testing."
                ),
            )
        return self.state
