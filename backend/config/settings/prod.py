"""Production settings.

Import with ``DJANGO_SETTINGS_MODULE=config.settings.prod``. Everything here is
env-driven, so the same image runs in any environment without a rebuild.

The one behaviour that is not configurable: **this refuses to start with the
development secret key.** A default that silently works in production is the
kind of thing nobody notices until a session cookie is forgeable, so it is a
startup error rather than a warning.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import ALLOWED_HOSTS, MIDDLEWARE

MIDDLEWARE = [
    *MIDDLEWARE,
    # Clickjacking: the app is never legitimately framed, and a driver looking
    # at a route panel is exactly the target for an overlaid fake control.
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# CSRF middleware is deliberately absent, and `check --deploy` reports it
# (security.W003). That check assumes a session- or cookie-authenticated site.
# This API has no authentication, sets no cookies, and runs with
# CORS_ALLOW_CREDENTIALS = False, so there is nothing for a browser to attach
# automatically -- the attack has no credential to ride in on. Adding the
# middleware would be theatre. Revisit if auth is ever added.
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# Required configuration
# ---------------------------------------------------------------------------

_SECRET = os.environ.get("DJANGO_SECRET_KEY", "")
if _SECRET in {"", "dev-only-not-for-production"}:
    msg = (
        "DJANGO_SECRET_KEY is unset or still the development default. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(50))'"
    )
    raise ImproperlyConfigured(msg)

SECRET_KEY = _SECRET

DEBUG = False

# A wildcard host header is how host-header injection works; require an explicit
# list rather than defaulting to "*" the way Django's scaffold does.
if not ALLOWED_HOSTS:  # pragma: no cover - guarded by the check below
    msg = "DJANGO_ALLOWED_HOSTS must list the hostnames this deployment answers on."
    raise ImproperlyConfigured(msg)

# ---------------------------------------------------------------------------
# Proxy / TLS
# ---------------------------------------------------------------------------
# Behind a reverse proxy (nginx, Fly, Render) the client IP and scheme arrive in
# headers. Without USE_X_FORWARDED_* , Django believes every request came from
# the proxy itself, which breaks rate limiting and makes https detection fail.
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Default OFF, and this is deliberate.
#
# The redirect only makes sense when TLS is terminated in front of the app *and*
# that terminator speaks https on the same hostname. In front of a plain-HTTP
# nginx (the bundled compose stack, or a platform that terminates TLS itself),
# enabling it turns every request into a 301 to an https URL nothing is
# listening on -- the app looks entirely broken, and the container healthcheck
# fails because it probes over http. Enable it where it is correct.
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# Sessions and cookies
# ---------------------------------------------------------------------------
# The API is stateless, but Django still signs cookies if anything sets one.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
# Frontend assets are served by nginx; the backend only needs to serve its own
# (currently none), and it must not serve them itself in production.
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", "/var/www/static")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# A streaming endpoint that fails mid-response can only be diagnosed from logs,
# so they go to stdout for the platform's collector rather than to a file.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "tripplan": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
