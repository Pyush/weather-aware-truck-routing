"""Risk levels and their presentation metadata.

Severity ordering lives here and nowhere else. ``RiskLevel`` is an ``IntEnum``
whose integer values *are* the severity ordering, so the two operations the rest
of the system performs -- "which is worse?" and "average the risk" -- are plain
``max()`` and integer arithmetic.

    >>> RiskLevel.SEVERE > RiskLevel.HIGH
    True
    >>> RiskLevel.worst([RiskLevel.LOW, RiskLevel.NO_TRAVEL]) is RiskLevel.NO_TRAVEL
    True
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum
from types import MappingProxyType


class RiskLevel(IntEnum):
    """Weather risk at a point on a route, ordered worst-last."""

    LOW = 1
    MODERATE = 2
    HIGH = 3
    SEVERE = 4
    NO_TRAVEL = 5

    @property
    def label(self) -> str:
        """Human-readable name, e.g. ``"No Travel"``."""
        return _LEVEL_META[self]["label"]

    @property
    def hex_color(self) -> str:
        """Map colour for this level."""
        return _LEVEL_META[self]["hex"]

    @property
    def symbol(self) -> str:
        """Non-colour glyph, so risk is legible without relying on hue."""
        return _LEVEL_META[self]["symbol"]

    @property
    def description(self) -> str:
        """One-line driver-facing guidance."""
        return _LEVEL_META[self]["description"]

    @property
    def is_blocking(self) -> bool:
        """Whether this level makes the corridor untravable."""
        return self is RiskLevel.NO_TRAVEL

    @classmethod
    def from_score(cls, score: int) -> RiskLevel:
        """Rebuild a level from its integer score (inverse of ``int(level)``)."""
        return cls(score)

    @classmethod
    def worst(cls, levels: Iterable[RiskLevel]) -> RiskLevel:
        """Return the most severe level in an iterable.

        ``worst([])`` returns ``LOW`` so callers can fold over an empty
        collection without special-casing it.
        """
        worst = cls.LOW
        for level in levels:
            if level > worst:
                worst = level
        return worst


# Presentation metadata, declared after the enum and kept beside it so the UI
# never hard-codes a colour or a label (D12). Served from ``/api/v1/config/``.
_LEVEL_META: MappingProxyType[RiskLevel, MappingProxyType[str, str]] = MappingProxyType(
    {
        RiskLevel.LOW: MappingProxyType(
            {
                "label": "Low",
                "hex": "#16a34a",
                "symbol": "\u25cf",  # filled circle
                "description": "Normal driving conditions.",
            }
        ),
        RiskLevel.MODERATE: MappingProxyType(
            {
                "label": "Moderate",
                "hex": "#ca8a04",
                "symbol": "\u25d0",  # half-filled circle
                "description": "Caution. Reduce speed and increase following distance.",
            }
        ),
        RiskLevel.HIGH: MappingProxyType(
            {
                "label": "High",
                "hex": "#ea580c",
                "symbol": "\u25d1",  # reverse-filled circle
                "description": "Slow down and review conditions before continuing.",
            }
        ),
        RiskLevel.SEVERE: MappingProxyType(
            {
                "label": "Severe",
                "hex": "#dc2626",
                "symbol": "\u25a0",  # filled square
                "description": "Consider stopping in a safe location.",
            }
        ),
        RiskLevel.NO_TRAVEL: MappingProxyType(
            {
                "label": "No Travel",
                "hex": "#6d28d9",
                "symbol": "\u2716",  # heavy multiplication x
                "description": "Conditions exceed safe travel limits for this load.",
            }
        ),
    }
)


def level_catalog() -> list[dict[str, object]]:
    """Serialisable description of every level, for ``/api/v1/config/``."""
    return [
        {
            "value": int(level),
            "name": level.name,
            "label": level.label,
            "hex": level.hex_color,
            "symbol": level.symbol,
            "description": level.description,
        }
        for level in RiskLevel
    ]
