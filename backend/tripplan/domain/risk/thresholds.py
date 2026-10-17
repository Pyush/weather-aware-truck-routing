"""Weather risk thresholds, transcribed from the assessment's rule table.

This module is the **only** place a risk number is written down. Everything else
reads these tables, which is what keeps the backend, the golden tests and the
frontend legend in agreement (D12).

The published table is *not* uniform, and that asymmetry is preserved rather than
tidied away (D1)::

    Condition    Low        Moderate   High       Severe      No Travel
    Wind         <25 mph    25-34      35-44      45-54       >= 55 mph
    Rain         <0.10      0.10-0.25  0.25-0.50  0.50-1.00   > 1.00
    Snow         <0.5       0.5-1.0    1.0-2.0    2.0-3.0     > 3.0

Three details a naive ``elif`` chain gets wrong:

1. **The no-travel bound is inclusive for wind and exclusive for rain and snow.**
   55.0 mph is No Travel; exactly 1.00 in/hr of rain and exactly 3.0 in/hr of snow
   are Severe.
2. **Mid-buckets are half-open.** The labels are rounded integers (25-34) while
   the upstream API returns decimals, so 34.9 mph is Moderate, not Severe.
3. **The Severe bucket is closed at the top** for rain and snow, matching the
   ``0.50-1.00`` and ``2.0-3.0`` labels.

Each bound therefore carries an explicit inclusivity flag instead of relying on a
convention.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import RiskLevel


@dataclass(frozen=True, slots=True)
class Bucket:
    """A single classification band.

    Args:
        label: Name as printed in the assessment table.
        level: Risk level assigned to values inside the band.
        lower: Inclusive/exclusive lower bound, or ``None`` for negative infinity.
        lower_inclusive: Whether ``lower`` itself belongs to this bucket.
        upper: Inclusive/exclusive upper bound, or ``None`` for positive infinity.
        upper_inclusive: Whether ``upper`` itself belongs to this bucket.
    """

    label: str
    level: RiskLevel
    lower: float | None = None
    lower_inclusive: bool = True
    upper: float | None = None
    upper_inclusive: bool = False

    def contains(self, value: float) -> bool:
        """Whether ``value`` falls inside this bucket."""
        if self.lower is not None and (
            value < self.lower or (value == self.lower and not self.lower_inclusive)
        ):
            return False
        return not (
            self.upper is not None
            and (value > self.upper or (value == self.upper and not self.upper_inclusive))
        )

    def describe(self) -> str:
        """Render the band the way the assessment table does, e.g. ``"25-34"``."""
        if self.lower is None and self.upper is None:  # pragma: no cover - unused
            return "any"
        if self.lower is None:
            op = "<=" if self.upper_inclusive else "<"
            return f"{op}{self.upper:g}"
        if self.upper is None:
            op = ">=" if self.lower_inclusive else ">"
            return f"{op}{self.lower:g}"
        left = "[" if self.lower_inclusive else "("
        right = "]" if self.upper_inclusive else ")"
        return f"{left}{self.lower:g}, {self.upper:g}{right}"


# ---------------------------------------------------------------------------
# Wind -- mph. No Travel is *inclusive* at 55 (">= 55 mph").
# ---------------------------------------------------------------------------
WIND_BUCKETS: tuple[Bucket, ...] = (
    Bucket("Low", RiskLevel.LOW, upper=25.0),
    Bucket("Moderate", RiskLevel.MODERATE, lower=25.0, upper=35.0),
    Bucket("High", RiskLevel.HIGH, lower=35.0, upper=45.0),
    Bucket("Severe", RiskLevel.SEVERE, lower=45.0, upper=55.0),
    Bucket("No Travel", RiskLevel.NO_TRAVEL, lower=55.0),
)

# ---------------------------------------------------------------------------
# Rain -- inches/hour of *liquid* precipitation. No Travel is *exclusive* at 1.00,
# and the Severe band is closed at 1.00 ("0.50-1.00").
# ---------------------------------------------------------------------------
RAIN_BUCKETS: tuple[Bucket, ...] = (
    Bucket("Low", RiskLevel.LOW, upper=0.10),
    Bucket("Moderate", RiskLevel.MODERATE, lower=0.10, upper=0.25),
    Bucket("High", RiskLevel.HIGH, lower=0.25, upper=0.50),
    Bucket("Severe", RiskLevel.SEVERE, lower=0.50, upper=1.00, upper_inclusive=True),
    Bucket("No Travel", RiskLevel.NO_TRAVEL, lower=1.00, lower_inclusive=False),
)

# ---------------------------------------------------------------------------
# Snow -- inches/hour of snow *depth* (not liquid equivalent; ~7:1, see D15).
# No Travel is *exclusive* at 3.0, and the Severe band is closed at 3.0.
# ---------------------------------------------------------------------------
SNOW_BUCKETS: tuple[Bucket, ...] = (
    Bucket("Low", RiskLevel.LOW, upper=0.5),
    Bucket("Moderate", RiskLevel.MODERATE, lower=0.5, upper=1.0),
    Bucket("High", RiskLevel.HIGH, lower=1.0, upper=2.0),
    Bucket("Severe", RiskLevel.SEVERE, lower=2.0, upper=3.0, upper_inclusive=True),
    Bucket("No Travel", RiskLevel.NO_TRAVEL, lower=3.0, lower_inclusive=False),
)

BUCKETS_BY_VARIABLE: dict[str, tuple[Bucket, ...]] = {
    "wind": WIND_BUCKETS,
    "rain": RAIN_BUCKETS,
    "snow": SNOW_BUCKETS,
}

#: Unit each variable is expressed in, as printed in the assessment table.
UNITS: dict[str, str] = {
    "wind": "mph",
    "rain": "in/hr",
    "snow": "in/hr",
}


def find_bucket(variable: str, value: float) -> Bucket | None:
    """Return the band containing ``value``, or ``None`` if the bands have a gap.

    The tables are written to tile the whole number line, so ``None`` would mean
    the tables themselves are malformed -- the caller treats it as a data error
    rather than guessing a level.
    """
    try:
        buckets = BUCKETS_BY_VARIABLE[variable]
    except KeyError:
        msg = f"Unknown weather variable {variable!r}"
        raise ValueError(msg) from None
    for bucket in buckets:
        if bucket.contains(value):
            return bucket
    return None


def threshold_catalog() -> list[dict[str, object]]:
    """Serialisable threshold tables for ``GET /api/v1/config/``."""
    return [
        {
            "variable": variable,
            "unit": UNITS[variable],
            "buckets": [
                {
                    "label": bucket.label,
                    "level": bucket.level.name,
                    "range": bucket.describe(),
                }
                for bucket in buckets
            ],
        }
        for variable, buckets in BUCKETS_BY_VARIABLE.items()
    ]
