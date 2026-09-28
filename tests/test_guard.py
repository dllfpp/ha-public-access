"""Proxy detection and the public session caps."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from aiohttp.test_utils import make_mocked_request  # noqa: E402

from custom_components.public_access.guard import (  # noqa: E402
    SessionLimiter,
    proxy_not_trusted,
)


def _request(remote, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    request = make_mocked_request("GET", "/public_solar", headers=headers)
    return request.clone(remote=remote)


def test_direct_visit_is_fine():
    assert proxy_not_trusted(_request("192.168.1.10")) is None


def test_trusted_proxy_rewrites_remote_to_the_visitor():
    assert proxy_not_trusted(_request("203.0.113.7", "203.0.113.7")) is None


def test_untrusted_proxy_is_reported():
    assert proxy_not_trusted(_request("172.69.9.16", "203.0.113.7")) == "172.69.9.16"


def test_per_client_cap():
    limiter = SessionLimiter(total=10, per_client=2)
    assert limiter.acquire("a") and limiter.acquire("a")
    assert not limiter.acquire("a")
    assert limiter.acquire("b")
    limiter.release("a")
    assert limiter.acquire("a")


def test_total_cap_and_release():
    limiter = SessionLimiter(total=2, per_client=5)
    assert limiter.acquire("a") and limiter.acquire("b")
    assert not limiter.acquire("c")
    limiter.release("b")
    assert limiter.acquire("c")
    for client in ("a", "c"):
        limiter.release(client)
    assert limiter.active == 0
