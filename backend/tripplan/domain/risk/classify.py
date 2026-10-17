"""Classifying a single weather variable against the threshold tables.

Every function here is *total*: it never raises for a bad number, because a
missing upstream reading must degrade to an explicit data-quality flag rather
than crash a route analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import RiskLevel
from .thresholds import UNITS, find_bucket

#: Used when the upstream provider gives us nothing usable. Missing data must not
#: be reported as hazardous, but it is surfaced via ``data_quality`` so the UI can
#: say "weather unavailable" instead of implying the road is clear.
MISSING_VALUE_LEVEL = RiskLevel.LOW


@dataclass(frozen=True, slots=True)
class Classification:
    """Result of classifying one weather variable.

    Attributes:
        variable: ``"wind"``, ``"rain"`` or ``"snow"``.
        level: Classified risk level.
        bucket_label: The matching band, e.g. ``"Moderate"``.
        value: The value that was classified (``None`` if missing).
        unit: Unit of ``value``, for display.
        data_quality: ``"ok"`` or ``"missing"``.
    """

    variable: str
    level: RiskLevel
    bucket_label: str
    value: float | None
    unit: str
    data_quality: str = "ok"

    @property
    def is_missing(self) -> bool:
        """Whether the upstream value was unusable."""
        return self.data_quality != "ok"


def _coerce(value: float | int | None) -> float | None:
    """Normalise a provider reading to a finite float, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NaN and infinities are not usable readings.
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def classify(variable: str, value: float | int | None) -> Classification:
    """Classify ``value`` for ``variable`` against its threshold table.

    Args:
        variable: ``"wind"``, ``"rain"`` or ``"snow"``.
        value: The reading, in the variable's display unit.

    Returns:
        The classification. A missing or non-finite reading yields
        :data:`MISSING_VALUE_LEVEL` with ``data_quality="missing"``.
    """
    if variable not in UNITS:
        msg = f"Unknown weather variable {variable!r}"
        raise ValueError(msg)

    unit = UNITS[variable]
    number = _coerce(value)

    if number is None:
        return Classification(
            variable=variable,
            level=MISSING_VALUE_LEVEL,
            bucket_label="Unavailable",
            value=None,
            unit=unit,
            data_quality="missing",
        )

    bucket = find_bucket(variable, number)
    if bucket is None:  # pragma: no cover - only reachable if the tables are edited wrong
        msg = f"No {variable} bucket matches {number!r}; check the threshold tables"
        raise ValueError(msg)

    return Classification(
        variable=variable,
        level=bucket.level,
        bucket_label=bucket.label,
        value=number,
        unit=unit,
    )


def classify_wind(mph: float | int | None) -> Classification:
    """Classify a wind speed in mph."""
    return classify("wind", mph)


def classify_rain(in_per_hr: float | int | None) -> Classification:
    """Classify a liquid precipitation rate in in/hr."""
    return classify("rain", in_per_hr)


def classify_snow(in_per_hr: float | int | None) -> Classification:
    """Classify a snowfall rate in in/hr of snow *depth*."""
    return classify("snow", in_per_hr)
