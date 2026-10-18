"""Shared Django settings.

Values that the domain logic depends on are read from the environment here and
passed down as explicit arguments, so that `tripplan.domain.*` stays pure Python
and importable without Django (see docs/DECISIONS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from .env import env_bool, env_float, env_int, env_list

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-for-production")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "tripplan",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

# The app is deliberately database-free (docs/DECISIONS.md D9): there is no ORM,
# no models and no migrations. These are set only because Django's system checks
# expect them to exist.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "unused.sqlite3",
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
STATIC_URL = "static/"

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # The NDJSON renderer is what makes the streaming endpoint negotiable: DRF
    # answers 406 to `Accept: application/x-ndjson` unless some renderer claims
    # that media type.
    #
    # JSONRenderer must stay FIRST. DRF picks the first renderer whose media
    # type matches, and `Accept: */*` matches everything -- so with NDJSON
    # first, ordinary JSON responses would be labelled `application/x-ndjson`
    # and DRF would reject its own output.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "tripplan.api.renderers.NDJSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "tripplan.api.errors.api_exception_handler",
}

CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = False

# ---------------------------------------------------------------------------
# Cache (the only persistence in the system -- docs/DECISIONS.md D9)
# ---------------------------------------------------------------------------
if os.environ.get("CACHE_BACKEND", "locmem") == "redis":  # pragma: no cover
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tripplan",
        }
    }

# ---------------------------------------------------------------------------
# Upstream providers
# ---------------------------------------------------------------------------
GEOCODER_PROVIDER = os.environ.get("GEOCODER_PROVIDER", "open_meteo")
ROUTING_PROVIDER = os.environ.get("ROUTING_PROVIDER", "osrm")
WEATHER_PROVIDER = os.environ.get("WEATHER_PROVIDER", "open_meteo")

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
OSRM_TIMEOUT_SECONDS = env_float("OSRM_TIMEOUT_SECONDS", default=30.0)

OPEN_METEO_BASE_URL = os.environ.get("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1")
OPEN_METEO_GEOCODE_URL = os.environ.get(
    "OPEN_METEO_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1"
)
OPEN_METEO_TIMEOUT_SECONDS = env_float("OPEN_METEO_TIMEOUT_SECONDS", default=45.0)

HTTP_MAX_RETRIES = env_int("HTTP_MAX_RETRIES", default=3)

WEATHER_CACHE_TTL_SECONDS = env_int("WEATHER_CACHE_TTL_SECONDS", default=6 * 3600)
ANALYSIS_CACHE_TTL_SECONDS = env_int("ANALYSIS_CACHE_TTL_SECONDS", default=900)

# ---------------------------------------------------------------------------
# Domain tuning (see docs/DECISIONS.md)
# ---------------------------------------------------------------------------
_SPACINGS = env_list("SAMPLE_SPACINGS_MILES", default=["10", "25", "50"])
SAMPLE_SPACINGS_MILES = [int(v) for v in _SPACINGS]
DEFAULT_SAMPLE_SPACING_MILES = env_int("DEFAULT_SAMPLE_SPACING_MILES", default=25)
SLIDER_MAX_OFFSET_HOURS = env_int("SLIDER_MAX_OFFSET_HOURS", default=48)

TRUCK_SPEED_FACTOR = env_float("TRUCK_SPEED_FACTOR", default=0.85)
TRUCK_MIN_AVG_MPH = env_float("TRUCK_MIN_AVG_MPH", default=25.0)
TRUCK_MAX_AVG_MPH = env_float("TRUCK_MAX_AVG_MPH", default=65.0)

SAFETY_GATE_ENABLED = env_bool("SAFETY_GATE_ENABLED", default=True)
LOAD_BOUNDARY_INCLUSIVE = env_bool("LOAD_BOUNDARY_INCLUSIVE", default=False)
CHECKPOINT_TIME_SNAP = os.environ.get("CHECKPOINT_TIME_SNAP", "floor")

WEATHER_LOCATION_BUDGET = env_int("WEATHER_LOCATION_BUDGET", default=90)
WEATHER_BATCH_SIZE = env_int("WEATHER_BATCH_SIZE", default=20)
