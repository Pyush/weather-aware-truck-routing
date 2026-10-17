"""``manage.py bench`` -- measure live API latencies.

The Phase 3 latency budget rests on measurement rather than hope (BE-60). This
command records what the real providers actually cost for a few route profiles,
so the numbers quoted in PLAN.md and docs/BENCHMARKS.md can be regenerated
rather than remembered.

Usage::

    python manage.py bench                      # three profiles, live providers
    python manage.py bench --mock               # same shapes, deterministic provider
    python manage.py bench --json               # machine-readable output
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.core.management.base import BaseCommand

from tripplan.adapters.geocoding import GeocodeResult
from tripplan.adapters.mock_weather import MockWeatherProvider
from tripplan.adapters.routing import Route
from tripplan.services.trip_analysis import ServiceRegistry, TripAnalysisService, TripQuery

#: (label, origin, destination, spacing) covering short, medium and long trips.
PROFILES: tuple[tuple[str, str, str, int], ...] = (
    ("short", "Chicago, IL", "Milwaukee, WI", 10),
    ("medium", "Chicago, IL", "Detroit, MI", 25),
    ("long", "Denver, CO", "Chicago, IL", 50),
)


@dataclass(frozen=True, slots=True)
class ProfileResult:
    """Timings for one profile."""

    label: str
    origin: str
    destination: str
    spacing_mi: int
    routes: int
    checkpoints: int
    weather_locations: int
    first_event_ms: float
    routes_ready_ms: float
    total_ms: float
    severe_miles: float
    no_travel_miles: float
    recommended: str
    error: str | None = None


class StubGeocoder:
    """Resolves the known profile cities without a network call."""

    PLACES: dict[str, GeocodeResult] = {
        "chicago, il": GeocodeResult(41.8781, -87.6298, "Chicago, IL", "US", "Illinois"),
        "milwaukee, wi": GeocodeResult(43.0389, -87.9065, "Milwaukee, WI", "US", "Wisconsin"),
        "detroit, mi": GeocodeResult(42.3314, -83.0458, "Detroit, MI", "US", "Michigan"),
        "denver, co": GeocodeResult(39.7392, -104.9903, "Denver, CO", "US", "Colorado"),
    }

    name = "bench"

    def geocode(self, query: str) -> GeocodeResult:
        """Look the place up, case-insensitively."""
        key = query.strip().lower()
        if key not in self.PLACES:
            msg = f"bench geocoder has no entry for {query!r}"
            raise LookupError(msg)
        return self.PLACES[key]

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Single candidate per place."""
        return [self.geocode(query)]


class StubRouter:
    """Synthesises three plausible routes so benchmarking needs no routing API."""

    name = "bench"

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> list[Route]:
        """Return three corridors of differing length."""
        lat_span = destination[0] - origin[0]
        lon_span = destination[1] - origin[1]
        routes: list[Route] = []
        for _index, (lat_bend, lon_bend, miles, hours) in enumerate(
            [
                (0.0, 0.0, 300.0, 5.2),
                (0.5, 0.8, 318.0, 5.6),
                (1.1, 1.6, 341.0, 6.1),
            ]
        ):
            geometry = [
                [
                    origin[1] + (lon_span * i / 60) + (lon_bend * i / 60),
                    origin[0] + (lat_span * i / 60) + (lat_bend * i / 60),
                ]
                for i in range(61)
            ]
            routes.append(
                Route(
                    geometry=geometry,
                    distance_m=miles * 1609.344,
                    duration_s=hours * 3600,
                    source="bench_synthetic",
                )
            )
        return routes


class Command(BaseCommand):
    """Measure and report live provider latencies."""

    help = "Benchmark the analysis pipeline against live (or mock) providers."

    def add_arguments(self, parser) -> None:
        """Declare CLI flags."""
        parser.add_argument(
            "--mock",
            action="store_true",
            help="Use the deterministic weather provider instead of Open-Meteo.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON instead of a table.",
        )
        parser.add_argument(
            "--load",
            type=float,
            default=40_000.0,
            help="Load weight in pounds (default 40000).",
        )

    def handle(self, *args, **options) -> None:
        """Run every profile and report."""
        weather = MockWeatherProvider() if options["mock"] else None
        if weather is None:
            from tripplan.adapters.http import HttpClient
            from tripplan.adapters.weather import OpenMeteoWeatherProvider

            weather = OpenMeteoWeatherProvider(HttpClient.from_settings())

        registry = ServiceRegistry(
            geocoder=StubGeocoder(),
            router=StubRouter(),
            weather=weather,
        )
        service = TripAnalysisService(registry)

        departure = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(hours=6)
        results: list[ProfileResult] = []

        for label, origin, destination, spacing in PROFILES:
            query = TripQuery(
                origin=origin,
                destination=destination,
                departure=departure,
                load_lb=options["load"],
                spacing_mi=spacing,
                slider_hours=48,
            )
            results.append(self._run(service, query, label, origin, destination, spacing))

        if options["json"]:
            self.stdout.write(json.dumps([r.__dict__ for r in results], indent=2, default=str))
            return

        self.stdout.write(self._table(results))
        self.stdout.write("")
        self.stdout.write(
            "NOTE: geocoding and routing are SYNTHETIC stubs so that the benchmark isolates\n"
            "weather cost and never spends someone's rate-limit budget. Only the weather\n"
            "column is live. Measured live figures for the other stages:\n"
            "  geocode 2 endpoints ~1.1 s, 3 routes ~1.3 s (see docs/BENCHMARKS.md).\n"
            "The synthetic route geometry is also ~300 mi regardless of profile, so the\n"
            "checkpoint counts understate a genuinely 1,000 mi trip at the same spacing."
        )
        self.stdout.write(
            "Latency budget: geometry should be usable within a few seconds; weather dominates,\n"
            "which is exactly why /analyze/stream/ sends geometry first instead of waiting."
        )

    def _run(
        self,
        service: TripAnalysisService,
        query: TripQuery,
        label: str,
        origin: str,
        destination: str,
        spacing: int,
    ) -> ProfileResult:
        """Time one profile, capturing the milestones that matter to the UI."""
        started = time.monotonic()
        first_event_ms = 0.0
        routes_ready_ms = 0.0
        checkpoints = 0
        weather_locations = 0
        severe = 0.0
        no_travel = 0.0
        recommended = "-"
        routes = 0

        try:
            for event in service.stream(query):
                kind = event["type"]
                elapsed_ms = (time.monotonic() - started) * 1000
                if kind == "meta" and first_event_ms == 0.0:
                    first_event_ms = elapsed_ms
                elif kind == "route":
                    routes += 1
                    routes_ready_ms = elapsed_ms
                elif kind == "weather_plan":
                    weather_locations = event["weather_locations"]
                    checkpoints = event["checkpoint_count"]
                elif kind == "summary":
                    severe += event["severe_miles"]
                    no_travel += event["no_travel_miles"]
                elif kind == "recommendation":
                    recommended = event["label"] or "-"
                elif kind == "error":
                    return ProfileResult(
                        label,
                        origin,
                        destination,
                        spacing,
                        routes,
                        checkpoints,
                        weather_locations,
                        first_event_ms,
                        routes_ready_ms,
                        (time.monotonic() - started) * 1000,
                        severe,
                        no_travel,
                        recommended,
                        error=event.get("error", {}).get("message"),
                    )
        except Exception as exc:
            return ProfileResult(
                label,
                origin,
                destination,
                spacing,
                routes,
                checkpoints,
                weather_locations,
                first_event_ms,
                routes_ready_ms,
                (time.monotonic() - started) * 1000,
                severe,
                no_travel,
                recommended,
                error=str(exc),
            )

        return ProfileResult(
            label=label,
            origin=origin,
            destination=destination,
            spacing_mi=spacing,
            routes=routes,
            checkpoints=checkpoints,
            weather_locations=weather_locations,
            first_event_ms=round(first_event_ms, 1),
            routes_ready_ms=round(routes_ready_ms, 1),
            total_ms=round((time.monotonic() - started) * 1000, 1),
            severe_miles=round(severe, 1),
            no_travel_miles=round(no_travel, 1),
            recommended=recommended,
        )

    def _table(self, results: list[ProfileResult]) -> str:
        """Render results as a fixed-width table."""
        header = (
            f"{'profile':8s} {'spacing':>7s} {'routes':>6s} {'cps':>5s} "
            f"{'wx':>4s} {'first':>9s} {'routes@':>9s} {'total':>9s}  recommended"
        )
        lines = [header, "-" * len(header)]
        for r in results:
            lines.append(
                f"{r.label:8s} {r.spacing_mi:6d}mi {r.routes:6d} {r.checkpoints:5d} "
                f"{r.weather_locations:4d} {r.first_event_ms:8.0f}ms "
                f"{r.routes_ready_ms:8.0f}ms {r.total_ms:8.0f}ms  {r.recommended}"
                + (f"  ERROR: {r.error}" if r.error else "")
            )
        return "\n".join(lines)
