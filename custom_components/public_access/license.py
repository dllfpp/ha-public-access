"""Subscription validation.

The entitlement is an Ed25519-signed blob issued by the license server and verified
here **offline** against a pinned public key, so a license-server outage can never
take down a customer's public page: a cached entitlement keeps working until it
expires, and then for a grace period on top.

Nothing about the customer's home is sent: the license key, a salted hash of the
Home Assistant instance id, and the two version numbers. No dashboard data, no
entity names, nothing about what is published.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "public_access.license"

# The license server's Ed25519 public key. Entitlements are verified against this
# without contacting anyone, which is what makes the grace period possible.
ISSUER_PUBLIC_KEY_B64: str = "VkikGmFVRvr2_r6Y--EWPdsIzy2k7Eiaq6XaYHgstXg"

ENTITLEMENT_PREFIX = "PA1"
GRACE_SECONDS = 14 * 24 * 3600
REFRESH_INTERVAL_SECONDS = 12 * 3600
REQUEST_TIMEOUT = 20

STATUS_ACTIVE = "active"
STATUS_TRIALING = "trialing"
STATUS_PAST_DUE = "past_due"
STATUS_GRACE = "grace"
STATUS_EXPIRED = "expired"
STATUS_INVALID = "invalid"
STATUS_UNLICENSED = "unlicensed"
STATUS_OFFLINE = "offline"

SERVING_STATUSES = frozenset(
    {STATUS_ACTIVE, STATUS_TRIALING, STATUS_PAST_DUE, STATUS_GRACE}
)


@dataclass
class LicenseState:
    """Everything the rest of the integration needs to know about the license."""

    status: str = STATUS_UNLICENSED
    plan: str | None = None
    expires_at: float | None = None
    features: set[str] = field(default_factory=set)
    message: str | None = None
    last_check: float | None = None
    # From the license server: when the trial or paid period ends, and where
    # to subscribe. Drives the owner-facing "trial ending" repair notice.
    subscription_ends_at: float | None = None
    checkout_url: str | None = None

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
            "last_check": self.last_check,
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
            message="This license is bound to a different Home Assistant instance.",
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
            message="Could not reach the license server; serving on the grace period.",
        )
    if status not in SERVING_STATUSES:
        return LicenseState(
            status=STATUS_INVALID,
            plan=plan,
            expires_at=expires_at,
            message="Subscription inactive.",
        )
    return LicenseState(status=status, plan=plan, expires_at=expires_at, features=features)


class LicenseManager:
    """Holds the current license state and knows how to refresh it."""

    def __init__(
        self,
        hass: HomeAssistant,
        license_key: str,
        fingerprint: str,
        server_url: str,
        plugin_version: str = "",
    ) -> None:
        self._hass = hass
        self._key = license_key.strip()
        self._fingerprint = fingerprint
        self._server = server_url.rstrip("/")
        self._plugin_version = plugin_version
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._cached: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.state = LicenseState()
        self.payload_version: str | None = None

    # -- public API ------------------------------------------------------------

    async def async_load(self) -> LicenseState:
        """Restore a cached entitlement, then refresh it if it is due."""
        self._cached = await self._store.async_load() or {}
        token = self._cached.get("entitlement")
        if isinstance(token, str):
            payload = verify_entitlement(token, ISSUER_PUBLIC_KEY_B64)
            if payload:
                self.state = state_from_payload(payload, self._fingerprint)
                self.state.last_check = self._cached.get("last_check")
                self.state.subscription_ends_at = self._cached.get("subscription_ends_at")
                self.state.checkout_url = self._cached.get("checkout_url")
        self.payload_version = self._cached.get("payload_version")
        await self.async_refresh()
        return self.state

    async def async_refresh(self, force: bool = False) -> LicenseState:
        """Contact the license server if due, and apply whatever it says."""
        async with self._lock:
            if self._key.startswith("DEV-"):
                self.state = LicenseState(
                    status=STATUS_ACTIVE,
                    plan="development",
                    features={"energy", "generic-cards"},
                    message="Development license: no license server contacted.",
                    last_check=time.time(),
                )
                return self.state

            if not self._key:
                self.state = LicenseState(
                    status=STATUS_UNLICENSED, message="No license key configured."
                )
                return self.state

            last_check = self._cached.get("last_check") or 0
            if not force and (time.time() - last_check) < REFRESH_INTERVAL_SECONDS:
                return self.state

            endpoint = "activate" if not self._cached.get("activated") else "heartbeat"
            try:
                await self._call(endpoint)
            except _LicenseRefused as refused:
                # The server has spoken: stop serving, and forget the cached
                # entitlement so a restart cannot resurrect it.
                self.state = LicenseState(status=STATUS_INVALID, message=refused.message)
                self._cached = {"activated": self._cached.get("activated", False)}
                await self._store.async_save(self._cached)
            except Exception as error:  # noqa: BLE001 - network, DNS, timeouts
                _LOGGER.warning(
                    "Could not reach the license server (%s); "
                    "continuing on the cached entitlement",
                    error,
                )
                if not self.state.may_serve and self.state.status == STATUS_UNLICENSED:
                    self.state = LicenseState(
                        status=STATUS_OFFLINE,
                        message=(
                            "Could not reach the license server and there is no "
                            "cached entitlement yet."
                        ),
                    )
            return self.state

    # -- internals -------------------------------------------------------------

    async def _call(self, endpoint: str) -> None:
        session = async_get_clientsession(self._hass)
        url = f"{self._server}/v1/{endpoint}"
        body = {
            "license_key": self._key,
            "fingerprint": self._fingerprint,
            "plugin_version": self._plugin_version,
            "ha_version": getattr(self._hass.config, "as_dict", dict)().get("version", ""),
        }
        async with session.post(
            url, json=body, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            if response.status == 403:
                detail = "This license is not valid."
                try:
                    detail = (await response.json()).get("detail", detail)
                except Exception:  # noqa: BLE001
                    pass
                raise _LicenseRefused(detail)
            response.raise_for_status()
            data = await response.json()

        token = data.get("entitlement", "")
        payload = verify_entitlement(token, ISSUER_PUBLIC_KEY_B64)
        if payload is None:
            raise RuntimeError("the license server returned an entitlement we cannot verify")

        self.state = state_from_payload(payload, self._fingerprint)
        self.state.last_check = time.time()
        self.state.subscription_ends_at = data.get("subscription_ends_at")
        self.state.checkout_url = data.get("checkout_url")
        self.payload_version = data.get("payload_version")
        self._cached = {
            "entitlement": token,
            "last_check": self.state.last_check,
            "activated": True,
            "payload_version": self.payload_version,
            "subscription_ends_at": self.state.subscription_ends_at,
            "checkout_url": self.state.checkout_url,
        }
        await self._store.async_save(self._cached)
        _LOGGER.info(
            "License check succeeded: %s (%s)", self.state.status, self.state.plan
        )


class _LicenseRefused(Exception):
    """The server refused this license; it is not a transient failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
