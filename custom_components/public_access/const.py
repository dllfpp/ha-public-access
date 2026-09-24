"""Constants for the Public Access integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "public_access"

CONF_LICENSE_KEY: Final = "license_key"
CONF_LICENSE_SERVER: Final = "license_server"
CONF_DASHBOARD: Final = "dashboard"
CONF_VIEW_PATH: Final = "view_path"
CONF_PUBLIC_PATH: Final = "public_path"
CONF_ENABLED: Final = "enabled"
CONF_NOINDEX: Final = "noindex"
CONF_CACHE_SECONDS: Final = "cache_seconds"
CONF_FRAME_ANCESTORS: Final = "frame_ancestors"
CONF_EARLIEST_DATE: Final = "earliest_date"
CONF_SHOW_DEVICES: Final = "show_devices"

# How the public page is produced. "live" renders sanitized data with our own
# renderer; "snapshot" serves a photograph of the real dashboard taken by the
# companion container. In snapshot mode the sanitizer protects nothing: whatever
# is on the owner's screen is published as pixels.
CONF_MODE: Final = "mode"
MODE_LIVE: Final = "live"
MODE_SNAPSHOT: Final = "snapshot"
CONF_SNAPSHOT_TTL: Final = "snapshot_ttl"
DEFAULT_SNAPSHOT_TTL: Final = 900
# Where the companion writes, relative to the configuration directory.
SNAPSHOT_DIR: Final = "public_access_snapshots"
SNAPSHOT_IMAGE: Final = "dashboard.png"
SNAPSHOT_REQUEST: Final = "render.request"

DEFAULT_PUBLIC_PATH: Final = "public"
# TODO before launch: replace with the production licence API hostname.
DEFAULT_LICENSE_SERVER: Final = "https://api.example.com"
# How often the integration re-checks the subscription. The server also refuses
# to be polled harder than this.
LICENSE_REFRESH_HOURS: Final = 12
DEFAULT_CACHE_SECONDS: Final = 300
DEFAULT_NOINDEX: Final = True

# Requests per minute per client IP for the public endpoints.
RATE_LIMIT_PER_MINUTE: Final = 60

# Paths the public route must never claim. A registered aiohttp view outranks the
# frontend catch-all resource (verified on HA 2026.9.3), so claiming one of these
# would shadow part of the user's own Home Assistant. The live panel list from
# hass.data[frontend.DATA_PANELS] is checked on top of this static list, since it
# also contains every storage dashboard.
RESERVED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "api",
        "auth",
        "static",
        "local",
        "frontend_latest",
        "frontend_es5",
        "service_worker.js",
        "sw-modern.js",
        "sw-legacy.js",
        "manifest.json",
        "robots.txt",
        "favicon.ico",
        "hacsfiles",
        "media",
        "image",
        "images",
        "onboarding.html",
        "redirect",
        "_my_redirect",
        "notfound",
        "lovelace",
        "profile",
        "config",
        "developer-tools",
        "history",
        "logbook",
        "energy",
        "map",
        "todo",
        "calendar",
        "media-browser",
        "home",
        "security",
        "climate",
        "light",
        "maintenance",
    }
)

# Statistics periods the public API accepts. The client may only name one of
# these; it never supplies statistic ids or raw date ranges. They are calendar
# periods, as in Home Assistant's own energy dashboard (see data.period_start);
# the second value is the recorder bucket size.
PERIODS: Final[dict[str, tuple[str, int]]] = {
    # name: (recorder period, nominal days — informational only)
    "day": ("hour", 1),
    "week": ("day", 7),
    "month": ("day", 31),
    "year": ("month", 366),
}

# Service exposed so support can tell a customer "reload the licence now".
SERVICE_REFRESH_LICENSE: Final = "refresh_license"
