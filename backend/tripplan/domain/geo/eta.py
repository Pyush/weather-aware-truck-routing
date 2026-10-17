"""The truck ETA model.

The assessment asks for "the truck's estimated arrival time at each checkpoint".
The free routing provider returns a **car** profile: the measured
Denver -> Chicago route implies 56.4 mph, which is not a truck's sustained
average (D6). Using it directly would produce ETAs a driver would not recognise.

So a truck speed model is applied on top:

``truck_avg_mph = clamp(car_implied_mph * TRUCK_SPEED_FACTOR, MIN, MAX)``

and checkpoint ETAs accumulate from the departure time. Every constant is a
parameter with a documented default, and the UI's Methodology panel states them,
because an ETA is only as honest as its assumptions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class TruckProfile:
    """Assumptions used to turn route distance into time.

    Attributes:
        speed_factor: Multiplier applied to the routing provider's implied speed.
        min_avg_mph: Floor on the resulting average speed.
        max_avg_mph: Ceiling on the resulting average speed.
        break_after_hours: Hours of driving before a mandatory rest. ``0``
            disables breaks.
        break_duration_minutes: Length of that rest.
    """

    speed_factor: float = 0.85
    min_avg_mph: float = 25.0
    max_avg_mph: float = 65.0
    break_after_hours: float = 0.0
    break_duration_minutes: float = 30.0

    def __post_init__(self) -> None:
        """Reject a self-contradictory profile at construction time."""
        if self.speed_factor <= 0:
            msg = f"speed_factor must be positive, got {self.speed_factor}"
            raise ValueError(msg)
        if self.min_avg_mph <= 0 or self.max_avg_mph <= 0:
            msg = "speed bounds must be positive"
            raise ValueError(msg)
        if self.min_avg_mph > self.max_avg_mph:
            msg = f"min_avg_mph {self.min_avg_mph} exceeds max_avg_mph {self.max_avg_mph}"
            raise ValueError(msg)

    def truck_avg_mph(self, provider_distance_mi: float, provider_duration_s: float) -> float:
        """Average truck speed for a provider route.

        Args:
            provider_distance_mi: Route distance in miles.
            provider_duration_s: The provider's car-profile duration in seconds.

        Returns:
            The clamped average speed in mph. A provider duration of zero or less
            (unknown traffic data) falls back to the profile's midpoint, so a
            missing duration degrades to a plausible estimate rather than a
            division by zero.
        """
        if provider_duration_s <= 0 or provider_distance_mi <= 0:
            return (self.min_avg_mph + self.max_avg_mph) / 2
        car_mph = provider_distance_mi / (provider_duration_s / 3600.0)
        return min(max(car_mph * self.speed_factor, self.min_avg_mph), self.max_avg_mph)

    def hours_for(self, miles: float, avg_mph: float) -> float:
        """Driving hours needed to cover ``miles``."""
        return miles / avg_mph


DEFAULT_TRUCK_PROFILE = TruckProfile()


def rest_hours(driving_hours: float, profile: TruckProfile) -> float:
    """Rest time earned so far, in hours.

    A break is earned each time the driver completes ``break_after_hours`` of
    driving, so after 11 h of driving with an 11 h limit the driver has earned
    exactly one break.
    """
    if profile.break_after_hours <= 0:
        return 0.0
    breaks = math.floor(driving_hours / profile.break_after_hours)
    return breaks * (profile.break_duration_minutes / 60.0)


def cumulative_eta(
    departure: datetime,
    distances_mi: Sequence[float],
    avg_mph: float,
    profile: TruckProfile = DEFAULT_TRUCK_PROFILE,
) -> list[datetime]:
    """Arrival time at each cumulative distance.

    Args:
        departure: Departure time. Naive datetimes are interpreted as UTC.
        distances_mi: Cumulative checkpoint distances in miles, strictly
            increasing, starting at 0.
        avg_mph: Average truck speed for the whole route.
        profile: Used only for the rest-break rule.

    Returns:
        One timezone-aware arrival time per distance. The first entry equals
        ``departure``. With breaks enabled the sequence is non-decreasing, and
        consecutive arrivals across a break are more than ``avg_mph``-apart.
    """
    start = departure if departure.tzinfo else departure.replace(tzinfo=UTC)
    hours_driven = 0.0
    arrivals: list[datetime] = []

    for index, distance in enumerate(distances_mi):
        if index:
            hours_driven += profile.hours_for(distance - distances_mi[index - 1], avg_mph)
        arrivals.append(start + timedelta(hours=hours_driven + rest_hours(hours_driven, profile)))

    return arrivals


def eta_plan(
    departure: datetime,
    provider_distance_mi: float,
    provider_duration_s: float,
    profile: TruckProfile = DEFAULT_TRUCK_PROFILE,
) -> tuple[float, float]:
    """Resolve the average speed and total duration for a route.

    Returns:
        ``(avg_mph, total_hours_including_breaks)``.
    """
    avg_mph = profile.truck_avg_mph(provider_distance_mi, provider_duration_s)
    driving_hours = profile.hours_for(provider_distance_mi, avg_mph)

    total_hours = driving_hours
    if profile.break_after_hours > 0:
        breaks = math.floor(driving_hours / profile.break_after_hours)
        total_hours += breaks * (profile.break_duration_minutes / 60.0)

    return avg_mph, total_hours


def hour_index(eta: datetime, window_start: datetime, snap: str = "floor") -> int:
    """Index of the forecast hour covering ``eta``.

    Args:
        eta: The checkpoint's arrival time.
        window_start: First hour of the fetched weather window.
        snap: ``"floor"`` (default), ``"ceil"`` or ``"nearest"``. The three modes
            differ by at most one hour, which is asserted by the test suite
            (D14).

    Returns:
        A non-negative hour offset from ``window_start``, clamped at 0.

    Raises:
        ValueError: If ``snap`` is not a recognised mode.
    """
    start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
    arrival = eta if eta.tzinfo else eta.replace(tzinfo=UTC)

    delta_hours = (arrival - start).total_seconds() / 3600.0
    if snap == "floor":
        index = math.floor(delta_hours)
    elif snap == "ceil":
        index = math.ceil(delta_hours)
    elif snap == "nearest":
        index = round(delta_hours)
    else:
        msg = f"Unknown snap mode {snap!r}; expected 'floor', 'ceil' or 'nearest'"
        raise ValueError(msg)

    return max(index, 0)
