"""Static guarantee: this integration can only read Home Assistant.

A public, unauthenticated endpoint must not be able to reach a write path, so the
package is scanned for any call that could mutate state. This is a blunt instrument
on purpose: it fails on the mere presence of such a call, before review.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "public_access"

FORBIDDEN = (
    "async_call(",         # service calls
    "services.call",
    # Not "call_service" bare: that is the websocket message *name*, which
    # mirror.py must mention in order to refuse it.
    "async_set(",          # state writes ("async_setup" must not match)
    "states.async_set",
    "async_save(",         # store / config writes
    "async_update_entry_data",
    "async_create_system_user(",  # auth store writes: allowed only in mirror.py
    "async_create_refresh_token(",
    "async_remove_user(",
    "async_update_user(",
    "save_prefs",
    "import_statistics",
    "clear_statistics",
    "adjust_sum_statistics",
    "config/save",
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "exec_module(",       # running code: only the signed payload, see ALLOWED
)

# The writes this integration does perform, each to its own state and each
# named here so a new one cannot appear unnoticed:
# - license.py caches the signed entitlement;
# - mirror.py remembers the id of the read-only viewer user it created, and
#   creating that user (and its refresh token) is itself a write to the auth
#   store — once, and never anything that touches the owner's own users;
# - assets.py imports mirror_core.py, the one piece of code that arrives in the
#   renderer payload, and only after payload.py has verified the payload's
#   Ed25519 signature against the pinned key.
ALLOWED = {
    ("license.py", "async_save("),
    ("mirror.py", "async_save("),
    ("mirror.py", "async_create_system_user("),
    ("mirror.py", "async_create_refresh_token("),
    ("assets.py", "exec_module("),
}


def python_sources():
    return sorted(PACKAGE.rglob("*.py"))


def test_package_contains_no_write_paths():
    offenders = []
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            for needle in FORBIDDEN:
                if needle in code and (path.name, needle) not in ALLOWED:
                    offenders.append(f"{path.name}:{line_number}: {needle} -> {line.strip()}")
    assert not offenders, "write-capable calls found:\n" + "\n".join(offenders)


def test_only_get_is_implemented_on_the_public_view():
    text = (PACKAGE / "view.py").read_text(encoding="utf-8")
    for verb in ("async def post", "async def put", "async def delete", "async def patch"):
        assert verb not in text, f"{verb} must not exist on the public view"
    assert "async def get" in text


def test_public_view_requires_no_auth_by_design_and_says_so():
    text = (PACKAGE / "view.py").read_text(encoding="utf-8")
    assert "requires_auth = False" in text


def test_client_cannot_choose_statistic_ids():
    """The only client-supplied parameter is the period, validated against an enum."""
    text = (PACKAGE / "view.py").read_text(encoding="utf-8")
    assert 'request.query.get("period"' in text
    assert "if period not in PERIODS" in text
    for leak in ('query.get("entity', 'query.get("statistic', 'query.get("start'):
        assert leak not in text


def test_state_attributes_are_whitelisted():
    text = (PACKAGE / "data.py").read_text(encoding="utf-8")
    assert "SAFE_ATTRIBUTES" in text
    assert "if key in SAFE_ATTRIBUTES" in text
