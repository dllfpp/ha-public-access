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


DEFAULT_PUBLIC_PATH: Final = "public"
DEFAULT_LICENSE_SERVER: Final = "https://api.dllfpp.cloud"
TRIAL_URL: Final = "https://publicaccess.dllfpp.cloud"
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

# Service exposed so support can tell a customer "reload the license now".
SERVICE_REFRESH_LICENSE: Final = "refresh_license"

# Concurrent public websocket sessions, in total and per visitor address. Each
# one is a live connection on the owner's instance; past the cap new visitors
# get 503 + Retry-After instead of adding load.
MAX_PUBLIC_SESSIONS = 25
MAX_SESSIONS_PER_CLIENT = 4
