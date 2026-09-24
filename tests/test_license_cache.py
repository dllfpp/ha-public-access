"""A cached entitlement belongs to one key only."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.public_access.license import cache_belongs_to  # noqa: E402


def test_a_cached_entitlement_is_only_trusted_for_its_own_key():
    payload = {"sub": "PA-DQDU-WJG3-D2PU-YNZW", "inst": "fdfb"}
    assert cache_belongs_to(payload, "PA-DQDU-WJG3-D2PU-YNZW")
    assert cache_belongs_to(payload, " pa-dqdu-wjg3-d2pu-ynzw ")
    assert not cache_belongs_to(payload, "PA-Y723-TECQ-Z33N-4A8G")
    assert not cache_belongs_to({}, "PA-Y723-TECQ-Z33N-4A8G")
