"""Local development settings."""

from __future__ import annotations

from .base import *
from .base import CORS_ALLOWED_ORIGINS

DEBUG = True

if not CORS_ALLOWED_ORIGINS:  # pragma: no cover - dev convenience
    CORS_ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
