"""Routing provider (OSRM) and the "always give me three distinct routes" logic.

**Why the extra work.** Measured: OSRM with ``alternatives=true`` returned **2**
routes for a 1,006-mile trip and **1** when via-waypoints were pinned (PLAN.md
§2). The assessment requires 3 options, so a strategy is needed (D11):

1. Ask OSRM for alternatives.
2. Deduplicate them geometrically -- identical geometry must not appear twice,
   or two identical options could "win" the ranking for spurious reasons.
3. If fewer than 3 *distinct* routes survive, synthesize variants by inserting
   perpendicular via-waypoints along the best route and re-requesting.

If genuinely fewer than 3 distinct routes exist (a short trip), we return what
exists rather than fabricating an option.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tripplan.domain.geo.haversine import bounds

from .errors import NoRouteFound
from .http import HttpClient

logger = logging.getLogger(__name__)

#: Mean Earth radius in miles, for the via-waypoint offsets.
_MI_PER_DEG_LAT = 69.0


@dataclass(frozen=True, slots=True)
class Route:
    """One routed option.

    Attributes:
        geometry: GeoJSON ``[lon, lat]`` polyline.
        distance_m: Provider-reported road distance, metres.
        duration_s: Provider-reported driving time, seconds (car profile).
        source: How this option was produced, for display and debugging.
    """

    geometry: list[list[float]]
    distance_m: float
    duration_s: float
    source: str = "osrm_alternative"

    @property
    def distance_mi(self) -> float:
        """Road distance in miles."""
        return self.distance_m / 1609.344

    @property
    def duration_hours(self) -> float:
        """Driving time in hours."""
        return self.duration_s / 3600.0

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "distance_m": round(self.distance_m, 1),
            "duration_s": round(self.duration_s, 1),
            "source": self.source,
        }


@runtime_checkable
class Router(Protocol):
    """Produces route options between two points."""

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> list[Route]:
        """Return up to three distinct route options.

        Args:
            origin: ``(lat, lon)``.
            destination: ``(lat, lon)``.

        Raises:
            NoRouteFound: The provider found no path.
            UpstreamError: The provider failed.
        """
        ...


@dataclass(frozen=True, slots=True)
class ViaWaypoint:
    """An intermediate point forcing a route onto a particular corridor."""

    lat: float
    lon: float


class OSRMRouter:
    """Routes via an OSRM server.

    Args:
        client: Shared HTTP client.
        base_url: OSRM server root.
        want_routes: How many distinct options to produce.
        profile: OSRM profile name, ``"driving"`` by default. Note the free demo
            servers expose a **car** profile; the truck ETA model corrects for
            that separately (D6).
    """

    def __init__(
        self,
        client: HttpClient,
        *,
        base_url: str = "https://router.project-osrm.org",
        want_routes: int = 3,
        profile: str = "driving",
    ) -> None:
        """Configure the router."""
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._want_routes = want_routes
        self._profile = profile

    # -- request plumbing ---------------------------------------------------

    def _fetch(
        self,
        coords: Sequence[tuple[float, float]],
        *,
        alternatives: bool,
    ) -> list[Route]:
        """Request routes through ``coords`` (lat, lon pairs)."""
        path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
        url = f"{self._base_url}/route/v1/{self._profile}/{path}"
        params = {
            "alternatives": "true" if alternatives else "false",
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
        payload = self._client.get_json(url, params=params, provider="osrm")

        if not isinstance(payload, dict):
            msg = f"OSRM returned {type(payload).__name__}, expected an object"
            raise NoRouteFound("osrm", msg)

        code = payload.get("code")
        if code == "NoRoute":
            raise NoRouteFound(
                "osrm",
                "no route between the requested points",
                origin=str(coords[0]),
                destination=str(coords[-1]),
            )
        if code != "Ok":
            msg = f"OSRM returned code={code!r}: {payload.get('message', '')}"
            raise NoRouteFound("osrm", msg)

        routes: list[Route] = []
        for leg in payload.get("routes") or []:
            geometry = (leg.get("geometry") or {}).get("coordinates")
            if not geometry or len(geometry) < 2:
                continue
            routes.append(
                Route(
                    geometry=[[float(c[0]), float(c[1])] for c in geometry],
                    distance_m=float(leg.get("distance") or 0.0),
                    duration_s=float(leg.get("duration") or 0.0),
                    source="osrm_alternative" if alternatives else "osrm_via_variant",
                )
            )
        return routes

    # -- public API ---------------------------------------------------------

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
    ) -> list[Route]:
        """Return up to ``want_routes`` distinct options."""
        direct = self._fetch([origin, destination], alternatives=True)
        distinct = dedupe_routes(direct)

        if len(distinct) >= self._want_routes:
            return distinct[: self._want_routes]

        logger.info(
            "OSRM returned %d distinct route(s), synthesising more to reach %d",
            len(distinct),
            self._want_routes,
        )

        for variant in _via_variants(distinct[0] if distinct else None, origin, destination):
            try:
                # _fetch speaks plain (lat, lon) tuples; keep the ViaWaypoint
                # dataclass out of its interface.
                fetched = self._fetch(
                    [origin, (variant.lat, variant.lon), destination],
                    alternatives=False,
                )
            except NoRouteFound:
                logger.info("via-waypoint variant %s had no route; skipping", variant)
                continue
            distinct = dedupe_routes([*distinct, *fetched])
            if len(distinct) >= self._want_routes:
                break

        if len(distinct) < self._want_routes:
            logger.info(
                "only %d distinct route(s) exist for this trip; returning them all",
                len(distinct),
            )
        return distinct[: self._want_routes]


# ---------------------------------------------------------------------------
# Geometric deduplication (BE-37)
# ---------------------------------------------------------------------------

#: Above this mean separation, two sampled geometries are considered the same
#: road. Tuned so that genuinely different corridors (tens of miles apart) survive
#: while trivially different turns do not.
SAME_ROUTE_TOLERANCE_MI = 3.0


def sample_geometry(
    geometry: Sequence[Sequence[float]], points: int = 64
) -> list[tuple[float, float]]:
    """Resample a polyline to ``points`` evenly spaced ``(lat, lon)`` positions.

    Comparing sampled positions rather than raw vertices makes the comparison
    independent of how densely each provider happened to return its geometry.
    """
    if not geometry:
        return []

    from tripplan.domain.geo.haversine import cumulative_distances, point_at_distance

    distances = cumulative_distances(geometry)
    total = distances[-1]
    if total <= 0:
        return [(float(geometry[0][1]), float(geometry[0][0]))] * points

    return [point_at_distance(geometry, distances, total * i / (points - 1)) for i in range(points)]


def mean_separation_mi(
    first: Sequence[tuple[float, float]],
    second: Sequence[tuple[float, float]],
) -> float:
    """Mean great-circle distance between two equally sampled geometries."""
    from tripplan.domain.geo.haversine import haversine_miles

    if not first or not second:
        return math.inf
    n = min(len(first), len(second))
    if n == 0:
        return math.inf
    total = sum(
        haversine_miles(first[i][0], first[i][1], second[i][0], second[i][1]) for i in range(n)
    )
    return total / n


def dedupe_routes(
    routes: Sequence[Route],
    tolerance_mi: float = SAME_ROUTE_TOLERANCE_MI,
) -> list[Route]:
    """Drop routes that are geometrically the same as one already accepted.

    The shortest accepted representative wins, so a duplicate never displaces a
    better-scoring option.
    """
    kept: list[tuple[Route, list[tuple[float, float]]]] = []
    for route in sorted(routes, key=lambda r: r.distance_m):
        sampled = sample_geometry(route.geometry)
        if any(mean_separation_mi(sampled, existing) < tolerance_mi for _, existing in kept):
            logger.debug("dropping duplicate route (%.1f mi)", route.distance_mi)
            continue
        kept.append((route, sampled))
    return [route for route, _ in kept]


# ---------------------------------------------------------------------------
# Via-waypoint variant generation (BE-36)
# ---------------------------------------------------------------------------

#: Fractions along the route where variants deviate.
_VARIANT_FRACTIONS = (0.35, 0.65)

#: Perpendicular offsets in miles. Large enough to force a different corridor,
#: small enough that the resulting route is still plausible.
_VARIANT_OFFSETS_MI = (28.0, 55.0)


@dataclass(frozen=True, slots=True)
class _VariantPlan:
    """One planned deviation."""

    fraction: float
    offset_mi: float
    side: int
    candidates: tuple[ViaWaypoint, ...] = field(default_factory=tuple)


def perpendicular_waypoints(
    geometry: Sequence[Sequence[float]],
    fraction: float,
    offset_mi: float,
    side: int,
) -> list[ViaWaypoint]:
    """Waypoints offset perpendicular to the route at ``fraction`` along it.

    ``side`` of ``+1`` puts the point to the left of travel, ``-1`` to the right.
    Several points are returned: the exact perpendicular is computed against the
    local heading, and OSRM snaps a point to the nearest road, so a short list
    gives the provider something usable even on a coarse polyline.
    """
    from tripplan.domain.geo.haversine import cumulative_distances, point_at_distance

    if len(geometry) < 2:
        return []

    distances = cumulative_distances(geometry)
    total = distances[-1]
    if total <= 0:
        return []

    target = total * fraction
    lat, lon = point_at_distance(geometry, distances, target)

    # Local heading from the vertices bracketing the target.
    index = next((i for i, d in enumerate(distances) if d >= target), len(distances) - 1)
    before = geometry[max(index - 1, 0)]
    after = geometry[min(index, len(geometry) - 1)]
    d_lon = after[0] - before[0]
    d_lat = after[1] - before[1]
    heading = math.atan2(d_lon * math.cos(math.radians(lat)), d_lat)

    # Perpendicular direction: `heading` is a compass bearing (clockwise from
    # north), so rotating by -90 degrees gives the left-hand side and +90 the
    # right-hand side. `side=+1` therefore means left, as documented.
    bearing = heading - side * math.pi / 2
    perp_lat = math.cos(bearing) * offset_mi / _MI_PER_DEG_LAT
    perp_lon = (
        math.sin(bearing) * offset_mi / (_MI_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    )

    waypoints = [
        ViaWaypoint(lat=lat + perp_lat, lon=lon + perp_lon),
        # A shallower nudge, for when the exact perpendicular lands off-road.
        ViaWaypoint(
            lat=lat + perp_lat * 0.5,
            lon=lon + perp_lon * 0.5,
        ),
    ]
    return waypoints


def _via_variants(
    base: Route | None,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> list[ViaWaypoint]:
    """Planned deviations to try when OSRM returns too few alternatives."""
    if base is None or len(base.geometry) < 2:
        return []

    plans: list[_VariantPlan] = []
    for i, fraction in enumerate(_VARIANT_FRACTIONS):
        offset = _VARIANT_OFFSETS_MI[i % len(_VARIANT_OFFSETS_MI)]
        side = 1 if i % 2 == 0 else -1
        plans.append(
            _VariantPlan(
                fraction=fraction,
                offset_mi=offset,
                side=side,
                candidates=tuple(perpendicular_waypoints(base.geometry, fraction, offset, side)),
            )
        )

    # Interleave so both sides of the corridor get tried early.
    ordered: list[ViaWaypoint] = []
    for depth in range(max(len(p.candidates) for p in plans)):
        for plan in plans:
            if depth < len(plan.candidates):
                ordered.append(plan.candidates[depth])

    logger.info(
        "planning %d via-waypoint variant(s) for a %.0f mi route",
        len(ordered),
        base.distance_mi,
    )
    return ordered


def route_bounds(route: Route) -> tuple[float, float, float, float]:
    """``(west, south, east, north)`` for a route, for map fitting."""
    return bounds(route.geometry)
