"""What the mirror may show, decided in the open.

Two small checks the public connection relies on, kept apart from the proxy so
they can be read and tested on their own: which view of the dashboard is the
published one, and keeping the owner's default dashboard away from visitors.
Nothing here reads or writes Home Assistant state.
"""

from __future__ import annotations

from typing import Any


def view_matches(view: dict[str, Any], view_path: str | None) -> bool:
    """Is this the view the owner chose to publish?

    The option is always text, but a view path written in YAML as a bare number
    (``path: 123456``) arrives as an int, so both sides are compared as text. A
    view without a path never matches a configured one.
    """
    if view_path is None:
        return True
    path = view.get("path")
    return path is not None and str(path) == str(view_path)


def pin_default_panel(data: Any, public_path: str) -> Any:
    """Point every ``default_panel`` in frontend user/system data at the public path.

    The owner's default dashboard (Settings -> Dashboards) reaches the visitor
    through the frontend's user and system data. For the visitor that panel does
    not exist, and the frontend stops on its loading screen trying to open it;
    its name is also nothing a visitor needs to learn.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "default_panel":
                data[key] = public_path
            else:
                pin_default_panel(value, public_path)
    elif isinstance(data, list):
        for item in data:
            pin_default_panel(item, public_path)
    return data
