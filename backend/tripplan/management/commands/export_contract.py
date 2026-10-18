"""Export the API contract for the frontend.

The frontend holds **no risk rules** (D12): thresholds, load policy, level colours
and assumptions all come from ``GET /api/v1/config/``. This command writes that
response plus a complete, deterministic example analysis payload to
``frontend/src/types/contract.json``, which the frontend's types and tests are
checked against. A contract test in the frontend asserts the example payload
still has the shape the selectors rely on, so a backend rename fails CI rather
than silently returning `undefined` in the UI.

Usage::

    python manage.py export_contract [--out path]
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from tripplan.adapters.geocoding import GeocodeResult
from tripplan.adapters.mock_weather import MockWeatherProvider
from tripplan.adapters.routing import Route
from tripplan.api.errors import error_codes
from tripplan.domain.risk.enums import RiskLevel, level_catalog
from tripplan.domain.risk.load_rules import DEFAULT_LOAD_POLICY
from tripplan.domain.risk.miles import summarise
from tripplan.domain.risk.recommend import AVG_RISK_PRECISION, SAFETY_GATE_ENABLED
from tripplan.domain.risk.thresholds import threshold_catalog
from tripplan.domain.risk.weather_vars import mapping_catalog
from tripplan.services.trip_analysis import ServiceRegistry, TripAnalysisService, TripQuery


def _find_repo_root(start: Path) -> Path:
    """Walk upward to the directory holding both ``backend/`` and ``frontend/``.

    Requiring *both* markers matters: a stray ``frontend/`` inside ``backend/``
    would otherwise look like a valid root and silently write the contract to
    the wrong place.
    """
    for parent in start.resolve().parents:
        if (parent / "backend").is_dir() and (parent / "frontend").is_dir():
            return parent
    msg = f"could not locate the repository root above {start}"
    raise RuntimeError(msg)


DEFAULT_OUT = _find_repo_root(Path(__file__)) / "frontend" / "src" / "types" / "contract.json"


class ExampleGeocoder:
    """Resolves the two example cities without a network call."""

    name = "example"

    PLACES = {
        "chicago, il": GeocodeResult(41.8781, -87.6298, "Chicago, IL", "US", "Illinois"),
        "detroit, mi": GeocodeResult(42.3314, -83.0458, "Detroit, MI", "US", "Michigan"),
    }

    def geocode(self, query: str) -> GeocodeResult:
        """Look the place up, case-insensitively."""
        return self.PLACES[query.strip().lower()]

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Single candidate per place."""
        return [self.geocode(query)]


class ExampleRouter:
    """Builds three plausible corridors so the example is fully deterministic."""

    name = "example"

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> list[Route]:
        """Return three corridors of differing length."""
        lat_span = destination[0] - origin[0]
        lon_span = destination[1] - origin[1]
        out: list[Route] = []
        for index in range(3):
            bend = index * 0.6
            geometry = [
                [
                    origin[1] + lon_span * i / 80 + bend * i / 80,
                    origin[0] + lat_span * i / 80 + bend * i / 55,
                ]
                for i in range(81)
            ]
            out.append(
                Route(
                    geometry=geometry,
                    distance_m=(300 + index * 19) * 1609.344,
                    duration_s=(5.2 + index * 0.4) * 3600,
                    source="osrm_alternative" if index < 2 else "osrm_via_variant",
                )
            )
        return out


class Command(BaseCommand):
    """Write the API contract and a worked example payload."""

    help = "Export the API contract and example payload for the frontend."

    def add_arguments(self, parser) -> None:
        """Declare CLI flags."""
        parser.add_argument(
            "--out",
            type=Path,
            default=DEFAULT_OUT,
            help="Destination JSON file.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the committed file differs from a fresh build.",
        )

    def handle(self, *args, **options) -> None:
        """Build and write the contract, or verify the committed copy is current."""
        destination: Path = options["out"]
        check = options.get("check", False)
        rendered, payload = render_contract()

        if check:
            if not destination.exists():
                self.stderr.write(self.style.ERROR(f"missing {destination}"))
                raise SystemExit(1)
            if destination.read_text() != rendered:
                self.stderr.write(
                    self.style.ERROR(
                        f"{destination} is stale. Run `make gen-types` and commit the result."
                    )
                )
                raise SystemExit(1)
            self.stdout.write(self.style.SUCCESS(f"{destination} is up to date"))
            return

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered)
        self.stdout.write(
            self.style.SUCCESS(
                f"wrote {destination} "
                f"({len(payload['routes'])} routes, "
                f"{sum(len(c['points']) for c in payload['checkpoints'])} checkpoints)"
            )
        )


def render_contract() -> tuple[str, dict[str, Any]]:
    """Return the exact text of ``contract.json`` and the example payload.

    Split out from ``handle`` so the drift test can compare the committed file
    against a fresh build without writing anything.
    """
    # A fixed departure keeps the file diffable; only the shape matters.
    departure = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)

    registry = ServiceRegistry(
        geocoder=ExampleGeocoder(),
        router=ExampleRouter(),
        weather=MockWeatherProvider(),
    )
    example = TripAnalysisService(registry).analyze(
        TripQuery(
            origin="Chicago, IL",
            destination="Detroit, MI",
            departure=departure,
            load_lb=40_000,
            spacing_mi=25,
            slider_hours=48,
        )
    )
    payload = example.payload()
    # Wall-clock timing varies run to run, so it cannot appear in a committed
    # fixture without making `--check` fail on every run. The UI reads it from
    # the live stream; the fixture asserts shape, not speed.
    payload["meta"]["elapsed_ms"] = 0.0

    policy = DEFAULT_LOAD_POLICY
    contract: dict[str, Any] = {
        "$comment": (
            "Generated by `python manage.py export_contract`. Do not edit by hand. "
            "The frontend must not hard-code any threshold from this file; it reads "
            "them at runtime from GET /api/v1/config/."
        ),
        "config": {
            "levels": level_catalog(),
            "thresholds": threshold_catalog(),
            "load_policy": {
                "no_travel_wind_mph": policy.no_travel_wind_mph,
                "heavy_load_wind_mph": policy.heavy_load_wind_mph,
                "heavy_load_threshold_lb": policy.heavy_load_threshold_lb,
                "extra_heavy_wind_mph": policy.extra_heavy_wind_mph,
                "extra_heavy_threshold_lb": policy.extra_heavy_threshold_lb,
                "boundary_inclusive": policy.boundary_inclusive,
                "max_load_lb": 80_000,
            },
            "variable_mapping": mapping_catalog(),
            "ranking": {
                "criteria": [
                    "no_travel_miles",
                    "severe_miles",
                    "high_miles",
                    "avg_risk",
                    "duration_s",
                ],
                "safety_gate_enabled": SAFETY_GATE_ENABLED,
                "avg_risk_precision": AVG_RISK_PRECISION,
            },
            "sampling": {
                "spacings_mi": list(getattr(settings, "SAMPLE_SPACINGS_MILES", [10, 25, 50])),
                "default_spacing_mi": getattr(settings, "DEFAULT_SAMPLE_SPACING_MILES", 25),
            },
            "slider": {"max_offset_hours": 48, "semantics": "departure_offset"},
            "assumptions": {
                "truck_speed_factor": getattr(settings, "TRUCK_SPEED_FACTOR", 0.85),
                "truck_min_avg_mph": getattr(settings, "TRUCK_MIN_AVG_MPH", 25.0),
                "truck_max_avg_mph": getattr(settings, "TRUCK_MAX_AVG_MPH", 65.0),
                "checkpoint_time_snap": getattr(settings, "CHECKPOINT_TIME_SNAP", "floor"),
                "weather_resolution": "hourly",
                "note": (
                    "Precipitation and snowfall are preceding-hour sums; snowfall is snow "
                    "DEPTH, not liquid equivalent. The slider shifts the departure time."
                ),
            },
        },
        "example": {
            "request": {
                "origin": "Chicago, IL",
                "destination": "Detroit, MI",
                "departure": departure.isoformat(),
                "load_lb": 40_000,
                "spacing_mi": 25,
            },
            "response": payload,
            # Independent backend answers for the departure slider. These
            # use the domain mileage attribution on the original (unrounded)
            # checkpoints, not the frontend selector under test.
            "offset_summaries": {
                str(offset): {
                    route.route_id: summarise(
                        [point.distance_mi for point in route.checkpoints],
                        [
                            RiskLevel.from_score(
                                slot.levels[min(slot.eta_index + offset, len(slot.levels) - 1)]
                            )
                            for slot in route.hourly
                        ],
                    ).to_dict()
                    for route in example.routes
                }
                for offset in (0, 6, 24, 48)
            },
        },
        "error_codes": error_codes(),
        "events": [
            "meta",
            "route",
            "weather_plan",
            "weather",
            "checkpoints",
            "summary",
            "recommendation",
            "done",
            "error",
        ],
    }

    return json.dumps(contract, indent=2, default=str) + "\n", payload
