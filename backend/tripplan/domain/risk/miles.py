"""Turning per-checkpoint levels into per-route mileage.

The recommendation criteria are stated in *miles* ("fewest Severe miles"), so a
defensible definition of "a mile" is required, not just a level per point.

**Midpoint attribution** (D4): checkpoint *i* owns the road between the
midpoints to its neighbours::

    d_0    d_1    d_2    d_3   d_4
    |------|-------|-------|------|
       [==0==][==1==][==2==][==3==]
            ^ each band is split exactly at the midpoint between two checkpoints

This partitions the route, so ``sum(miles_by_level) == total_route_miles``
exactly, and a comparison between a 10-mile and a 50-mile sampling is fair. The
naive alternative -- "each checkpoint represents `spacing` miles" -- overcounts
at both ends and inflates the total by about one interval, which would bias every
comparison toward whichever route happened to use coarser sampling.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

from .enums import RiskLevel


@dataclass(frozen=True, slots=True)
class Segment:
    """A stretch of road owned by one checkpoint.

    Attributes:
        index: Index of the owning checkpoint.
        start_mi: Inclusive start of the owned stretch.
        end_mi: Exclusive end of the owned stretch.
        miles: Length of the owned stretch.
    """

    index: int
    start_mi: float
    end_mi: float
    miles: float

    @property
    def midpoint_mi(self) -> float:
        """Centre of the owned stretch."""
        return (self.start_mi + self.end_mi) / 2.0


@dataclass(frozen=True, slots=True)
class RouteRiskSummary:
    """Mileage and severity statistics for one route.

    Attributes:
        total_miles: Total route length.
        miles_by_level: Road miles at each level; sums to ``total_miles``.
        no_travel_miles: Miles at or above No Travel.
        severe_miles: Miles at Severe exactly.
        high_miles: Miles at High exactly.
        avg_risk: Mile-weighted mean level score, 1.0 (Low) to 5.0 (No Travel).
        worst_level: Most severe level seen anywhere on the route.
        segments: Owned stretches, in order.
    """

    total_miles: float
    miles_by_level: dict[RiskLevel, float]
    no_travel_miles: float
    severe_miles: float
    high_miles: float
    avg_risk: float
    worst_level: RiskLevel
    segments: tuple[Segment, ...] = field(repr=False)

    @property
    def moderate_miles(self) -> float:
        """Miles at Moderate."""
        return self.miles_by_level.get(RiskLevel.MODERATE, 0.0)

    @property
    def low_miles(self) -> float:
        """Miles at Low."""
        return self.miles_by_level.get(RiskLevel.LOW, 0.0)

    @property
    def blocked_miles(self) -> float:
        """Alias for :attr:`no_travel_miles`, matching the ranking terminology."""
        return self.no_travel_miles

    @property
    def adverse_miles(self) -> float:
        """Miles at High or worse -- the headline safety figure."""
        return self.no_travel_miles + self.severe_miles + self.high_miles

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "total_miles": round(self.total_miles, 2),
            "miles_by_level": {
                level.name: round(miles, 2) for level, miles in sorted(self.miles_by_level.items())
            },
            "no_travel_miles": round(self.no_travel_miles, 2),
            "severe_miles": round(self.severe_miles, 2),
            "high_miles": round(self.high_miles, 2),
            "moderate_miles": round(self.moderate_miles, 2),
            "low_miles": round(self.low_miles, 2),
            "avg_risk": round(self.avg_risk, 3),
            "worst_level": self.worst_level.name,
            "adverse_miles": round(self.adverse_miles, 2),
        }


def attribute_segments(distances_mi: Sequence[float]) -> tuple[Segment, ...]:
    """Split a route's length between its checkpoints using midpoints.

    Args:
        distances_mi: Cumulative checkpoint distances in miles, strictly
            increasing, starting at 0.0.

    Returns:
        One :class:`Segment` per checkpoint, in order, partitioning
        ``[0, distances_mi[-1]]``.

    Raises:
        ValueError: If the distances are empty or not strictly increasing.
    """
    if not distances_mi:
        msg = "Cannot attribute miles: no checkpoints"
        raise ValueError(msg)
    if any(b <= a for a, b in itertools.pairwise(distances_mi)):
        msg = f"Checkpoint distances must be strictly increasing, got {list(distances_mi)}"
        raise ValueError(msg)

    count = len(distances_mi)
    segments: list[Segment] = []
    for index, distance in enumerate(distances_mi):
        start = 0.0 if index == 0 else (distances_mi[index - 1] + distance) / 2.0
        end = distance if index == count - 1 else (distance + distances_mi[index + 1]) / 2.0
        segments.append(Segment(index=index, start_mi=start, end_mi=end, miles=end - start))
    return tuple(segments)


def summarise(
    distances_mi: Sequence[float],
    levels: Sequence[RiskLevel],
) -> RouteRiskSummary:
    """Compute per-level mileage and average risk for a route.

    Args:
        distances_mi: Cumulative checkpoint distances in miles.
        levels: Risk level per checkpoint.

    Returns:
        The route summary. Empty input yields a zero-mile summary.

    Raises:
        ValueError: If the two sequences differ in length.
    """
    if len(distances_mi) != len(levels):
        msg = f"Got {len(distances_mi)} distances but {len(levels)} levels"
        raise ValueError(msg)
    if not levels:
        return RouteRiskSummary(
            total_miles=0.0,
            miles_by_level={},
            no_travel_miles=0.0,
            severe_miles=0.0,
            high_miles=0.0,
            avg_risk=0.0,
            worst_level=RiskLevel.LOW,
            segments=(),
        )

    segments = attribute_segments(distances_mi)
    total_miles = float(distances_mi[-1])

    miles_by_level: dict[RiskLevel, float] = {}
    for segment, level in zip(segments, levels, strict=True):
        miles_by_level[level] = miles_by_level.get(level, 0.0) + segment.miles

    weighted = sum(
        segment.miles * int(level) for segment, level in zip(segments, levels, strict=True)
    )

    return RouteRiskSummary(
        total_miles=total_miles,
        miles_by_level=miles_by_level,
        no_travel_miles=miles_by_level.get(RiskLevel.NO_TRAVEL, 0.0),
        severe_miles=miles_by_level.get(RiskLevel.SEVERE, 0.0),
        high_miles=miles_by_level.get(RiskLevel.HIGH, 0.0),
        avg_risk=weighted / total_miles if total_miles else 0.0,
        worst_level=RiskLevel.worst(levels),
        segments=segments,
    )
