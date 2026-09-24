"""The payload carries the renderer and, optionally, the mirror glue."""

from __future__ import annotations

import io
import tarfile

import pytest

pytest.importorskip("homeassistant")

from custom_components.public_access.payload import _extract  # noqa: E402


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_renderer_and_mirror_glue_are_extracted_by_name():
    out = _extract(_archive({"app.js": "js", "mirror_core.py": "API_VERSION = 1", "evil.sh": "rm -rf /"}))
    assert out == {"app.js": "js", "mirror_core.py": "API_VERSION = 1"}


def test_an_older_payload_without_the_glue_still_installs():
    assert _extract(_archive({"app.js": "js"})) == {"app.js": "js"}


def test_a_payload_without_the_renderer_is_refused():
    assert _extract(_archive({"mirror_core.py": "x"})) is None
