"""Placing checkpoints along a route at a fixed mileage spacing.

The assessment asks for weather sampled "every 10 / 25 / 50 miles". Checkpoints
land at multiples of the spacing, and the origin and destination are always
included even when they are not multiples -- the driver cares about both ends.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .haversine import cumulative_distances, point_at_distance

#: Drop checkpoints closer together than this, to suppress polyline vertices that
#: land a few feet apart.
MIN_CHECKPOINT_SEPARATION_MI = 0.05


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A sampled point along a route.

    Attributes:
        lat: Latitude in degrees.
        lon: Longitude in degrees.
        distance_mi: Cumulative distance from the origin, in miles.
        is_origin: Whether this is the route's first point.
        is_destination: Whether this is the route's last point.
    """

    lat: float
    lon: float
    distance_mi: float
    is_origin: bool = False
    is_destination: bool = False

    @property
    def lonlat(self) -> list[float]:
        """GeoJSON coordinate order, for building map features."""
        return [self.lon, self.lat]

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "distance_mi": round(self.distance_mi, 2),
            "is_origin": self.is_origin,
            "is_destination": self.is_destination,
        }


def sample_distances(total_mi: float, spacing_mi: float) -> list[float]:
    """Target distances for checkpoints along a ``total_mi`` route.

    Args:
        total_mi: Route length in miles.
        spacing_mi: Spacing between checkpoints in miles.

    Returns:
        ``[0, spacing, 2*spacing, ...]`` up to ``total_mi``, with ``total_mi``
        appended if it is not already a multiple, and de-duplicated.

    Raises:
        ValueError: If ``spacing_mi`` is not positive.
    """
    if spacing_mi <= 0:
        msg = f"spacing_mi must be positive, got {spacing_mi}"
        raise ValueError(msg)
    if total_mi <= 0:
        return [0.0]

    targets: list[float] = [0.0]
    step = spacing_mi
    while step < total_mi:
        targets.append(step)
        step += spacing_mi

    # Always report the destination, even when it is not a multiple of the spacing.
    if abs(targets[-1] - total_mi) > 1e-9:
        targets.append(total_mi)

    return targets


def sample_checkpoints(
    coords: Sequence[Sequence[float]],
    spacing_mi: float,
) -> list[Checkpoint]:
    """Sample a route polyline every ``spacing_mi`` miles.

    Args:
        coords: GeoJSON ``[lon, lat]`` polyline from the routing provider.
        spacing_mi: Spacing between checkpoints, in miles.

    Returns:
        Checkpoints in travel order, strictly increasing in ``distance_mi``.

    Raises:
        ValueError: If the polyline is empty or the spacing is not positive.
    """
    if not coords:
        msg = "Cannot sample checkpoints along an empty polyline"
        raise ValueError(msg)

    distances = cumulative_distances(coords)
    total_mi = distances[-1]
    checkpoints: list[Checkpoint] = []

    for target in sample_distances(total_mi, spacing_mi):
        if checkpoints and target - checkpoints[-1].distance_mi < MIN_CHECKPOINT_SEPARATION_MI:
            continue
        lat, lon = point_at_distance(coords, distances, target)
        checkpoints.append(
            Checkpoint(
                lat=lat,
                lon=lon,
                distance_mi=target,
                is_origin=math.isclose(target, 0.0, abs_tol=1e-9),
                is_destination=math.isclose(target, total_mi, abs_tol=1e-9),
            )
        )

    # The destination must survive de-duplication even on a degenerate polyline.
    if checkpoints:
        last = checkpoints[-1]
        if not last.is_destination:
            lat, lon = point_at_distance(coords, distances, total_mi)
            checkpoints[-1] = Checkpoint(
                lat=lat,
                lon=lon,
                distance_mi=total_mi,
                is_origin=last.is_origin,
                is_destination=True,
            )
    return checkpoints


def sampling_plan(total_mi: float, spacing_mi: float) -> dict[str, object]:
    """Diagnostics about a sampling plan, for the API payload and methodology."""
    targets = sample_distances(total_mi, spacing_mi)
    return {
        "spacing_mi": spacing_mi,
        "route_miles": round(total_mi, 2),
        "checkpoint_count": len(targets),
    }
