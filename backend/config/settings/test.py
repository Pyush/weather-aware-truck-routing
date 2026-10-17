"""Test settings: fast, deterministic, and never touches the network."""

from __future__ import annotations

from .base import *
from .base import CACHES, DATABASES, DEBUG, SECRET_KEY

DEBUG = False
SECRET_KEY = "test-secret-key"

# Ephemeral in-memory cache so tests never share state through a warm LocMem.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tripplan-test",
    }
}

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
