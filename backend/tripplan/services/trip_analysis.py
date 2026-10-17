"""Trip analysis orchestration.

This is the only place the adapters and the domain meet. It is written as a
**generator of events** rather than a function returning a result, because the
streaming endpoint is not a special case: it consumes the same events that the
non-streaming endpoint collects into a single payload. One code path, so the two
endpoints cannot drift (BE-51/BE-53).

Event order::

    meta -> route x N -> checkpoints (per batch) -> summary x N
         -> recommendation -> done

The order is what makes the UI useful before the weather arrives: geometry and
route summaries reach the browser in ~2.5 s, and the checkpoint risk fills in
behind it (D8). Measured: 1.1 s geocoding + 1.3 s routing, then 19 s of weather
for a 25-location fetch.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from tripplan.adapters.errors import NoRouteFound, UpstreamError
from tripplan.adapters.geocoding import Geocoder, GeocodeResult
from tripplan.adapters.indexing import HourlyRisk, build_hourly_risk
from tripplan.adapters.routing import Route, Router
from tripplan.adapters.weather import (
    ForecastWindow,
    WeatherProvider,
    WeatherSeries,
    plan_locations,
    plan_window,
)
from tripplan.domain.geo.eta import (
    DEFAULT_TRUCK_PROFILE,
    TruckProfile,
    cumulative_eta,
    eta_plan,
    hour_index,
)
from tripplan.domain.geo.haversine import line_length_miles
from tripplan.domain.geo.sampling import Checkpoint, sample_checkpoints
from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.evaluate import evaluate_checkpoint
from tripplan.domain.risk.load_rules import DEFAULT_LOAD_POLICY, LoadPolicy
from tripplan.domain.risk.miles import RouteRiskSummary, summarise
from tripplan.domain.risk.recommend import (
    SAFETY_GATE_ENABLED,
    Recommendation,
    RouteCandidate,
    rank_routes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TripQuery:
    """A validated analysis request.

    Attributes:
        origin: Free-text origin.
        destination: Free-text destination.
        departure: Departure time, timezone-aware.
        load_lb: Load weight in pounds.
        spacing_mi: Checkpoint spacing, one of the configured options.
        slider_hours: Largest departure offset to support.
        truck: Truck speed assumptions.
    """

    origin: str
    destination: str
    departure: datetime
    load_lb: float
    spacing_mi: int = 25
    slider_hours: int = 48
    truck: TruckProfile = DEFAULT_TRUCK_PROFILE

    def cache_key(self) -> str:
        """Stable key for result caching.

        Derived from the *semantic* inputs only -- two requests that would
        produce identical output must share a cache entry, or the cache buys
        nothing.
        """
        return "|".join(
            [
                self.origin.strip().lower(),
                self.destination.strip().lower(),
                self.departure.astimezone(UTC).isoformat(),
                f"{self.load_lb:.0f}",
                str(self.spacing_mi),
                str(self.truck.speed_factor),
                str(self.truck.min_avg_mph),
                str(self.truck.max_avg_mph),
            ]
        )


@dataclass(frozen=True, slots=True)
class ServiceRegistry:
    """Everything the analysis needs, injected so tests can substitute anything.

    Attributes:
        geocoder: Place name resolver.
        router: Route provider.
        weather: Weather provider.
        load_policy: Load rule constants.
        safety_gate: Whether to prepend the No Travel ranking tier.
        snap: Checkpoint time-snap mode (D14).
        want_routes: How many route options to produce.
        weather_budget: Maximum unique weather locations to fetch.
    """

    geocoder: Geocoder
    router: Router
    weather: WeatherProvider
    load_policy: LoadPolicy = DEFAULT_LOAD_POLICY
    safety_gate: bool = SAFETY_GATE_ENABLED
    snap: str = "floor"
    want_routes: int = 3
    weather_budget: int = 90

    @classmethod
    def from_settings(cls) -> ServiceRegistry:
        """Build the registry from Django settings and the configured providers."""
        from django.conf import settings

        from tripplan.adapters.geocoding import GeocoderChain, OpenMeteoGeocoder, PhotonGeocoder
        from tripplan.adapters.http import HttpClient
        from tripplan.adapters.mock_weather import provider_from_name
        from tripplan.adapters.routing import OSRMRouter
        from tripplan.adapters.weather import OpenMeteoWeatherProvider

        http = HttpClient.from_settings()

        geocoder: Geocoder = GeocoderChain(
            [OpenMeteoGeocoder(http), PhotonGeocoder(http)],
        )

        weather_name = getattr(settings, "WEATHER_PROVIDER", "open_meteo")
        weather: WeatherProvider = provider_from_name(weather_name) or OpenMeteoWeatherProvider(
            http,
            batch_size=int(getattr(settings, "WEATHER_BATCH_SIZE", 20)),
        )

        return cls(
            geocoder=geocoder,
            router=OSRMRouter(
                http,
                base_url=getattr(settings, "OSRM_BASE_URL", "https://router.project-osrm.org"),
                want_routes=3,
            ),
            weather=weather,
            load_policy=LoadPolicy(
                boundary_inclusive=bool(getattr(settings, "LOAD_BOUNDARY_INCLUSIVE", False)),
            ),
            safety_gate=bool(getattr(settings, "SAFETY_GATE_ENABLED", True)),
            snap=str(getattr(settings, "CHECKPOINT_TIME_SNAP", "floor")),
            want_routes=3,
            weather_budget=int(getattr(settings, "WEATHER_LOCATION_BUDGET", 90)),
        )


@dataclass(frozen=True, slots=True)
class RouteOption:
    """One fully analysed route option."""

    index: int
    route_id: str
    label: str
    route: Route
    checkpoints: tuple[Checkpoint, ...]
    etas: tuple[datetime, ...]
    avg_mph: float
    total_hours: float
    road_miles: float
    summary: RouteRiskSummary
    hourly: tuple[HourlyRisk, ...] = field(default=())
    candidate: RouteCandidate | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API payload."""
        return {
            "index": self.index,
            "id": self.route_id,
            "label": self.label,
            "source": self.route.source,
            "geometry": [[round(c[0], 5), round(c[1], 5)] for c in self.route.geometry],
            "road_miles": round(self.road_miles, 1),
            "duration_s": round(self.total_hours * 3600, 1),
            "truck_avg_mph": round(self.avg_mph, 1),
            "summary": self.summary.to_dict(),
            "checkpoint_count": len(self.checkpoints),
        }


@dataclass(frozen=True, slots=True)
class TripAnalysis:
    """The complete result of one analysis."""

    query: TripQuery
    origin: GeocodeResult
    destination: GeocodeResult
    routes: tuple[RouteOption, ...]
    recommendation: Recommendation
    window: ForecastWindow
    snap: str = "floor"
    warnings: tuple[str, ...] = ()
    weather_locations: int = 0
    weather_cached: bool = False
    elapsed_ms: float = 0.0
    cached: bool = False

    def payload(self) -> dict[str, Any]:
        """The full JSON payload, matching the streamed events exactly."""
        return {
            "meta": self._meta(),
            "routes": [route.to_dict() for route in self.routes],
            "summaries": [route.summary.to_dict() for route in self.routes],
            "checkpoints": [
                {
                    "route_id": route.route_id,
                    "points": [
                        {
                            **checkpoint.to_dict(),
                            "eta": eta.isoformat(),
                            "eta_hour_index": hourly.eta_index,
                            "hourly_risk": list(hourly.levels),
                            "risk": hourly.base.to_dict(),
                        }
                        for checkpoint, eta, hourly in zip(
                            route.checkpoints, route.etas, route.hourly, strict=True
                        )
                    ],
                }
                for route in self.routes
            ],
            "recommendation": self._recommendation(),
            "warnings": list(self.warnings),
        }

    def _meta(self) -> dict[str, Any]:
        return {
            "origin": self.origin.to_dict(),
            "destination": self.destination.to_dict(),
            "departure": self.query.departure.isoformat(),
            "load_lb": self.query.load_lb,
            "spacing_mi": self.query.spacing_mi,
            "slider_hours": self.query.slider_hours,
            "hour_window_start": self.window.start.isoformat(),
            "hour_window_end": self.window.end.isoformat(),
            "hour_window_hours": self.window.hours,
            "time_snap": self.snap,
            "weather_locations": self.weather_locations,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "cached": self.cached,
            "truck": {
                "speed_factor": self.query.truck.speed_factor,
                "min_avg_mph": self.query.truck.min_avg_mph,
                "max_avg_mph": self.query.truck.max_avg_mph,
            },
        }

    def _recommendation(self) -> dict[str, Any]:
        rec = self.recommendation
        return {
            "route_id": rec.winner.route_id if rec.winner else None,
            "label": rec.winner.label if rec.winner else None,
            "ranking": [candidate.route_id for candidate in rec.ranking],
            "rationale": list(rec.rationale),
            "criterion_tally": rec.criterion_tally,
            "all_blocked": rec.all_blocked,
            "safety_gate_enabled": SAFETY_GATE_ENABLED,
        }


def _unavailable_hourly() -> HourlyRisk:
    """A placeholder risk slot for when no weather could be fetched.

    Carries ``data_quality="missing"`` so the UI can say the corridor was not
    assessed rather than implying the road is clear.
    """
    return HourlyRisk(
        eta_index=0,
        levels=(int(RiskLevel.LOW),),
        base=evaluate_checkpoint({}, 0.0),
    )


class TripAnalysisService:
    """Runs a :class:`TripQuery` against the configured adapters."""

    def __init__(self, registry: ServiceRegistry) -> None:
        """Wire the analysis to a set of adapters."""
        self._registry = registry

    def stream(self, query: TripQuery) -> Iterator[dict[str, Any]]:
        """Yield analysis events.

        This is the primitive: :meth:`analyze` simply drains it.

        Raises:
            GeocodeNotFound: An endpoint could not be resolved.
            NoRouteFound: No route exists between the endpoints.
            UpstreamError: A provider failed unrecoverably.
        """
        started = time.monotonic()
        registry = self._registry

        # -- 1. Geocode -----------------------------------------------------
        origin = registry.geocoder.geocode(query.origin)
        destination = registry.geocoder.geocode(query.destination)
        logger.info(
            "geocoded %r -> %r in %.0fms",
            query.origin,
            origin.display_name,
            (time.monotonic() - started) * 1000,
        )

        yield {
            "type": "meta",
            "origin": origin.to_dict(),
            "destination": destination.to_dict(),
            "departure": query.departure.isoformat(),
            "load_lb": query.load_lb,
            "spacing_mi": query.spacing_mi,
            "slider_hours": query.slider_hours,
            "time_snap": registry.snap,
        }

        # -- 2. Routes ------------------------------------------------------
        routes = registry.router.route(
            (origin.lat, origin.lon),
            (destination.lat, destination.lon),
        )
        if not routes:
            msg = "the routing provider returned no route"
            raise NoRouteFound("osrm", msg, origin=query.origin, destination=query.destination)

        if len(routes) < registry.want_routes:
            logger.info("only %d distinct route(s) exist for this trip", len(routes))

        # -- 3. Sample checkpoints and resolve ETAs -------------------------
        prepared: list[dict[str, Any]] = []
        for index, route in enumerate(routes):
            checkpoints = tuple(sample_checkpoints(route.geometry, float(query.spacing_mi)))
            road_miles = line_length_miles(route.geometry)
            avg_mph, total_hours = eta_plan(
                query.departure,
                provider_distance_mi=road_miles,
                provider_duration_s=route.duration_s,
                profile=query.truck,
            )
            etas = tuple(
                cumulative_eta(
                    query.departure,
                    [c.distance_mi for c in checkpoints],
                    avg_mph,
                    query.truck,
                )
            )
            prepared.append(
                {
                    "index": index,
                    "checkpoints": checkpoints,
                    "etas": etas,
                    "avg_mph": avg_mph,
                    "total_hours": total_hours,
                    "road_miles": road_miles,
                }
            )
            yield {
                "type": "route",
                "index": index,
                "id": f"r{index}",
                "label": f"Option {'ABCDEFG'[index]}",
                "source": route.source,
                "geometry": [[round(c[0], 5), round(c[1], 5)] for c in route.geometry],
                "road_miles": round(road_miles, 1),
                "truck_avg_mph": round(avg_mph, 1),
                "duration_s": round(total_hours * 3600, 1),
                "checkpoint_count": len(checkpoints),
            }

        # -- 4. Plan the weather fetch -------------------------------------
        window = plan_window(
            query.departure,
            longest_route_hours=max(p["total_hours"] for p in prepared),
            slider_hours=query.slider_hours,
        )
        all_checkpoints: list[Checkpoint] = [c for p in prepared for c in p["checkpoints"]]
        plan = plan_locations(
            [(c.lat, c.lon) for c in all_checkpoints],
            budget=registry.weather_budget,
        )
        warnings: list[str] = []
        if plan.coarsened:
            warnings.append(
                f"This trip needed more weather lookups than the budget allows, so checkpoints "
                f"were grouped (coverage is coarser than the {query.spacing_mi} mi spacing)."
            )

        yield {
            "type": "weather_plan",
            "hour_window_start": window.start.isoformat(),
            "hour_window_end": window.end.isoformat(),
            "hour_window_hours": window.hours,
            "checkpoint_count": len(all_checkpoints),
            "weather_locations": len(plan.unique),
            "deduped": plan.saved,
            "coarsened": plan.coarsened,
        }

        # -- 5. Fetch weather (the slow part) ------------------------------
        series = self._fetch_weather(plan.unique, window, warnings)
        yield {
            "type": "weather",
            "locations": len(series),
            "hours": len(series[0]) if series else 0,
        }

        # -- 6. Evaluate every checkpoint -----------------------------------
        # When the weather fetch failed there are no series to read. The route,
        # its checkpoints and its ETAs are still worth delivering, so each
        # checkpoint gets a single "unavailable" reading rather than the whole
        # request failing (BE-55).
        unavailable = _unavailable_hourly() if not series else None

        options: list[RouteOption] = []
        cursor = 0
        for prepared_route in prepared:
            checkpoints: tuple[Checkpoint, ...] = prepared_route["checkpoints"]
            etas: tuple[datetime, ...] = prepared_route["etas"]
            hourly: list[HourlyRisk] = []
            for _checkpoint, eta in zip(checkpoints, etas, strict=True):
                if unavailable is not None:
                    hourly.append(
                        replace(unavailable, eta_index=hour_index(eta, window.start, registry.snap))
                    )
                    cursor += 1
                    continue
                series_index = plan.index_of_checkpoint[cursor]
                cursor += 1
                hourly.append(
                    build_hourly_risk(
                        series[series_index],
                        window=window,
                        eta=eta,
                        load_lb=query.load_lb,
                        policy=registry.load_policy,
                    )
                )

            distances = [c.distance_mi for c in checkpoints]
            levels = [h.base.level for h in hourly]
            summary = summarise(distances, levels)

            option = RouteOption(
                index=prepared_route["index"],
                route_id=f"r{prepared_route['index']}",
                label=f"Option {'ABCDEFG'[prepared_route['index']]}",
                route=routes[prepared_route["index"]],
                checkpoints=checkpoints,
                etas=etas,
                avg_mph=prepared_route["avg_mph"],
                total_hours=prepared_route["total_hours"],
                road_miles=prepared_route["road_miles"],
                summary=summary,
                hourly=tuple(hourly),
                candidate=RouteCandidate(
                    route_id=f"r{prepared_route['index']}",
                    label=f"Option {'ABCDEFG'[prepared_route['index']]}",
                    summary=summary,
                    duration_s=prepared_route["total_hours"] * 3600,
                    distance_m=routes[prepared_route["index"]].distance_m,
                ),
            )
            options.append(option)

            yield {
                "type": "checkpoints",
                "route_id": option.route_id,
                "points": [
                    {
                        **checkpoint.to_dict(),
                        "eta": eta.isoformat(),
                        "eta_hour_index": h.eta_index,
                        "hourly_risk": list(h.levels),
                        "risk": h.base.to_dict(),
                    }
                    for checkpoint, eta, h in zip(checkpoints, etas, hourly, strict=True)
                ],
            }
            yield {"type": "summary", "route_id": option.route_id, **summary.to_dict()}

        # -- 7. Rank and recommend -----------------------------------------
        recommendation = rank_routes(
            [option.candidate for option in options if option.candidate],
            safety_gate=registry.safety_gate,
        )
        yield {
            "type": "recommendation",
            "route_id": recommendation.winner.route_id if recommendation.winner else None,
            "label": recommendation.winner.label if recommendation.winner else None,
            "ranking": [c.route_id for c in recommendation.ranking],
            "rationale": list(recommendation.rationale),
            "criterion_tally": recommendation.criterion_tally,
            "all_blocked": recommendation.all_blocked,
        }

        analysis = TripAnalysis(
            query=query,
            origin=origin,
            destination=destination,
            routes=tuple(options),
            recommendation=recommendation,
            window=window,
            snap=registry.snap,
            warnings=tuple(warnings),
            weather_locations=len(plan.unique),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )

        yield {
            "type": "done",
            "warnings": list(warnings),
            "weather_locations": len(plan.unique),
            "elapsed_ms": round(analysis.elapsed_ms, 1),
            # The finished analysis rides along so `analyze()` needs no hidden
            # side channel between it and this generator.
            "analysis": analysis,
        }

    def _fetch_weather(
        self,
        locations: Sequence[tuple[float, float]],
        window: ForecastWindow,
        warnings: list[str],
    ) -> list[WeatherSeries]:
        """Fetch weather, degrading to a warning if the provider fails outright.

        A storm forecast that never arrives should still produce a usable map
        with an honest "weather unavailable" banner, not a 502. Partial data is
        better than none, provided the UI is told (BE-55).
        """
        if not locations:
            return []
        try:
            return list(self._registry.weather.fetch(locations, window=window))
        except UpstreamError as exc:
            logger.warning("weather fetch failed: %s", exc)
            warnings.append(
                f"Weather data could not be retrieved ({exc.provider}). Routes and ETAs are "
                "shown, but risk could not be assessed."
            )
            return []

    def analyze(self, query: TripQuery) -> TripAnalysis:
        """Run the analysis to completion and return the full result.

        Drains :meth:`stream`, so the streaming and non-streaming endpoints
        cannot disagree.
        """
        analysis: TripAnalysis | None = None
        for event in self.stream(query):
            if event["type"] == "done":
                analysis = event["analysis"]
        if analysis is None:  # pragma: no cover - stream always finishes with done
            msg = "analysis stream ended without a result"
            raise RuntimeError(msg)
        return analysis
