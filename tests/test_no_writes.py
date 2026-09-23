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
    "call_service",
    "services.call",
    "async_set(",          # state writes ("async_setup" must not match)
    "states.async_set",
    "async_save(",         # store / config writes
    "async_update_entry_data",
    "save_prefs",
    "import_statistics",
    "clear_statistics",
    "adjust_sum_statistics",
    "config/save",
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
)

# Storing our own cached licence entitlement is a legitimate write.
ALLOWED = {("license.py", "async_save(")}


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
