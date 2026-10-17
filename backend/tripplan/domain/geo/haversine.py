"""Great-circle distance and polyline arc-length measurement.

Routes arrive from the routing provider as GeoJSON ``LineString`` coordinate
lists in ``[longitude, latitude]`` order. Everything downstream wants cumulative
*driven* miles along that polyline, so this module measures it directly rather
than relying on the provider's own total (which is computed over the road graph
and will not match a per-segment sum of great-circle chords).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

#: Mean Earth radius in miles.
EARTH_RADIUS_MI = 3958.7613

Coord = tuple[float, float]  # (latitude, longitude)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in miles.

    Args:
        lat1: Latitude of the first point, in degrees.
        lon1: Longitude of the first point, in degrees.
        lat2: Latitude of the second point, in degrees.
        lon2: Longitude of the second point, in degrees.

    Returns:
        The distance in miles. Antipodal and identical points are both handled.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def line_length_miles(coords: Sequence[Sequence[float]]) -> float:
    """Total length of a ``[lon, lat]`` polyline, in miles."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in itertools.pairwise(coords):
        total += haversine_miles(lat1, lon1, lat2, lon2)
    return total


def cumulative_distances(coords: Sequence[Sequence[float]]) -> list[float]:
    """Cumulative arc length along a ``[lon, lat]`` polyline.

    Args:
        coords: GeoJSON-ordered coordinates.

    Returns:
        One distance per coordinate, starting at ``0.0`` and non-decreasing, so
        ``result[-1]`` is the total polyline length in miles.

    Raises:
        ValueError: If ``coords`` is empty.
    """
    if not coords:
        msg = "Cannot measure an empty polyline"
        raise ValueError(msg)

    distances = [0.0]
    running = 0.0
    for (lon1, lat1), (lon2, lat2) in itertools.pairwise(coords):
        running += haversine_miles(lat1, lon1, lat2, lon2)
        distances.append(running)
    return distances


def point_at_distance(
    coords: Sequence[Sequence[float]],
    distances: Sequence[float],
    target_mi: float,
) -> Coord:
    """Interpolate the position ``target_mi`` along a polyline.

    Args:
        coords: GeoJSON-ordered coordinates.
        distances: Cumulative distances from :func:`cumulative_distances`.
        target_mi: Distance along the polyline, in miles. Values below 0 clamp to
            the first vertex, values beyond the end clamp to the last.

    Returns:
        The interpolated ``(latitude, longitude)``.

    Raises:
        ValueError: If ``coords`` is empty or the two sequences disagree in length.
    """
    if not coords:
        msg = "Cannot interpolate along an empty polyline"
        raise ValueError(msg)
    if len(coords) != len(distances):  # pragma: no cover - guarded by callers
        msg = "coords and distances must be the same length"
        raise ValueError(msg)

    if target_mi <= 0.0:
        return float(coords[0][1]), float(coords[0][0])
    if target_mi >= distances[-1]:
        return float(coords[-1][1]), float(coords[-1][0])

    for index in range(1, len(distances)):
        start, end = distances[index - 1], distances[index]
        if target_mi <= end:
            span = end - start
            lon1, lat1 = coords[index - 1]
            lon2, lat2 = coords[index]
            if span <= 0.0:  # duplicate consecutive vertices
                return float(lat2), float(lon2)
            ratio = (target_mi - start) / span
            return (
                float(lat1 + (lat2 - lat1) * ratio),
                float(lon1 + (lon2 - lon1) * ratio),
            )

    return float(coords[-1][1]), float(coords[-1][0])  # pragma: no cover


def bounds(coords: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    """Bounding box of a ``[lon, lat]`` polyline as ``(west, south, east, north)``."""
    if not coords:
        msg = "Cannot compute bounds of an empty polyline"
        raise ValueError(msg)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def simplify_tolerance_mi(distance_mi: float) -> float:
    """A Douglas-Peucker-style tolerance for a given route length.

    Longer routes get proportionally coarser tolerances so that very long
    polylines do not push tens of thousands of vertices into the browser.
    """
    if distance_mi <= 50:
        return 0.01
    if distance_mi <= 200:
        return 0.05
    if distance_mi <= 600:
        return 0.15
    if distance_mi <= 1500:
        return 0.4
    return 1.0
