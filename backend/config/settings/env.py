"""Tiny environment-variable helpers.

Deliberately dependency-free: four scalar types are all this project needs, and
a hand-rolled helper keeps the settings module importable with nothing installed
beyond Django itself.
"""

from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_bool(name: str, *, default: bool) -> bool:
    """Read a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    msg = f"{name}={raw!r} is not a valid boolean"
    raise ValueError(msg)


def env_int(name: str, *, default: int) -> int:
    """Read an integer environment variable."""
    raw = os.environ.get(name)
    return default if raw is None else int(raw.strip())


def env_float(name: str, *, default: float) -> float:
    """Read a float environment variable."""
    raw = os.environ.get(name)
    return default if raw is None else float(raw.strip())


def env_list(name: str, *, default: list[str]) -> list[str]:
    """Read a comma-separated environment variable as a list of strings."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]
