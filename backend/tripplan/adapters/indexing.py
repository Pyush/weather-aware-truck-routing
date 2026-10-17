"""Precomputing per-hour risk so the frontend slider costs nothing.

The provider gives us hourly weather for the whole window in one fetch (D7).
This module turns that into, for every checkpoint, an array of **risk levels per
hour**. The frontend then answers "what is the risk if I leave N hours later?"
with ``hourly_risk[eta_index + N]`` -- an array lookup, no network, no duplicated
rule logic, no chance of the UI disagreeing with the rules (D12).

The invariant that makes the slider work, asserted by the test suite::

    level_at_offset(checkpoint, h) == hourly_risk[eta_hour_index + h]
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from tripplan.domain.risk.classify import MISSING_VALUE_LEVEL
from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.evaluate import CheckpointRisk, evaluate_checkpoint
from tripplan.domain.risk.load_rules import DEFAULT_LOAD_POLICY, LoadPolicy

from .weather import ForecastWindow, WeatherSeries

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HourlyRisk:
    """Risk levels for one checkpoint across the forecast window.

    Attributes:
        eta_index: Index of the hour containing the checkpoint's ETA.
        levels: One level per hour in the window.
        base: Full assessment at the ETA, including the reasoning strings.
    """

    eta_index: int
    levels: tuple[int, ...]
    base: CheckpointRisk

    def level_at_offset(self, offset_hours: int) -> RiskLevel:
        """Risk level at ``eta_index + offset_hours``, clamped to the window.

        This is the function the frontend slider calls. It is O(1) and never
        touches the network.
        """
        if not self.levels:
            return self.base.level
        index = max(0, min(self.eta_index + offset_hours, len(self.levels) - 1))
        return RiskLevel.from_score(self.levels[index])

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "eta_hour_index": self.eta_index,
            "hourly_risk": list(self.levels),
            "base": self.base.to_dict(),
        }


def build_hourly_risk(
    series: WeatherSeries,
    *,
    window: ForecastWindow,
    eta: object,
    load_lb: float,
    policy: LoadPolicy = DEFAULT_LOAD_POLICY,
) -> HourlyRisk:
    """Classify every hour of a checkpoint's forecast.

    Args:
        series: Hourly readings at the checkpoint's location.
        window: The fetched hour range.
        eta: The checkpoint's arrival time (timezone-aware).
        load_lb: Load weight in pounds.
        policy: Load rule constants.

    Returns:
        The per-hour risk plus the full base assessment at the ETA.

    Notes:
        Load rules are re-applied at every hour, not just the ETA: a checkpoint
        can be ``Low`` on arrival and ``No Travel`` six hours later, and the
        slider is supposed to reveal exactly that.
    """
    hours = max(len(series), 1)
    eta_index = window.index_of(eta)  # type: ignore[arg-type]

    if not series.times:
        # No usable forecast at all. Report it honestly rather than crashing on
        # an empty series: one slot carrying "unavailable", which reads as Low
        # with data_quality="missing" so the UI can say so.
        base = evaluate_checkpoint({}, load_lb, policy)
        return HourlyRisk(
            eta_index=eta_index,
            levels=(int(MISSING_VALUE_LEVEL),),
            base=base,
        )

    levels: list[int] = []
    for i in range(hours):
        reading = series.reading_at(i)
        risk = evaluate_checkpoint(reading, load_lb, policy)
        levels.append(int(risk.level))

    if not levels:
        levels = [int(MISSING_VALUE_LEVEL)]

    base = evaluate_checkpoint(series.reading_at(eta_index), load_lb, policy)

    if base.data_quality != "ok":
        logger.debug("checkpoint at %s has %s weather data", series.lat, base.data_quality)

    return HourlyRisk(eta_index=eta_index, levels=tuple(levels), base=base)


def level_name(score: int) -> str:
    """Name for a level score, for payloads."""
    return RiskLevel.from_score(score).name


def summarise_hourly(risks: Sequence[HourlyRisk], offset_hours: int) -> dict[str, int]:
    """Count how many checkpoints sit at each level for a given offset.

    Used by the frontend to show how the risk profile shifts as the slider moves.
    """
    counts = {level.name: 0 for level in RiskLevel}
    for risk in risks:
        counts[risk.level_at_offset(offset_hours).name] += 1
    return counts
