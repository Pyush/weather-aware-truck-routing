"""Live third-party API smoke tests (BE-43).

**Opt-in. Nothing here runs by default.** The default suite is
``-m "not integration"``, which deselects this file; ``test_no_network.py``
enforces that mechanically rather than trusting the marker.

These tests hit Open-Meteo (geocoding + weather) and OSRM (routing). They
verify three things the recorded-fixture tests cannot:

1. **The upstream still speaks the shape we parse.** Every parse in
   ``adapters/`` is a bet about a third party's JSON. Recorded fixtures only
   prove we parsed last month's response.
2. **The documented units are the units that arrive.** mph, inches, and snow as
   depth rather than liquid equivalent (D15) are claims about the provider, not
   about us, so nothing offline can confirm them.
3. **The latency budget is real**, recorded to ``docs/BENCHMARKS.md`` by
   ``make bench-integration``.

What they deliberately do *not* do is assert on specific weather. A test that
fails because it rained is a test that will be deleted, and deleting it is how
coverage quietly disappears. Assertions here are about shape, ranges and
internal consistency.

Run with::

    pytest -m integration -v
    make bench-integration
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tripplan.adapters.geocoding import GeocoderChain, OpenMeteoGeocoder, PhotonGeocoder
from tripplan.adapters.http import HttpClient
from tripplan.adapters.routing import OSRMRouter
from tripplan.adapters.weather import ForecastWindow, OpenMeteoWeatherProvider, plan_window
from tripplan.services.trip_analysis import ServiceRegistry, TripAnalysisService, TripQuery

pytestmark = pytest.mark.integration

# A fixed corridor, so a latency comparison between runs means something.
ORIGIN = "Chicago, IL"
DESTINATION = "Detroit, MI"
PLACE = (41.8781, -87.6298)


@pytest.fixture(scope="module")
def http() -> Iterator[HttpClient]:
    """A real client, closed at the end of the module."""
    client = HttpClient.from_settings()
    yield client
    client.close()


def timed(label: str, timings: dict[str, list[float]], fn: Any) -> Any:
    """Run ``fn``, recording its wall-clock time under ``label``."""
    start = time.perf_counter()
    try:
        return fn()
    finally:
        timings.setdefault(label, []).append(time.perf_counter() - start)


# ---------------------------------------------------------------------------
# 1. Geocoding
# ---------------------------------------------------------------------------


def test_live_geocoding_resolves_known_cities(http: HttpClient) -> None:
    """A live geocode returns coordinates in the right place."""
    chain = GeocoderChain([OpenMeteoGeocoder(http), PhotonGeocoder(http)])

    for query, expected in ((ORIGIN, (41.88, -87.63)), (DESTINATION, (42.33, -83.05))):
        result = chain.geocode(query)
        assert -90 <= result.lat <= 90, f"{query}: latitude out of range"
        assert -180 <= result.lon <= 180, f"{query}: longitude out of range"
        # Within ~1 degree of the known city. Loose on purpose: providers move
        # their centroids, and a tighter bound would make this a flaky test of
        # somebody else's database rather than of our parsing.
        assert abs(result.lat - expected[0]) < 1.0, f"{query} resolved to {result.lat}"
        assert abs(result.lon - expected[1]) < 1.0, f"{query} resolved to {result.lon}"
        assert result.display_name, "a place must be nameable in the UI"


def test_live_geocoding_rejects_nonsense(http: HttpClient) -> None:
    """An unlocatable query raises rather than inventing coordinates."""
    from tripplan.adapters.errors import GeocodeNotFound

    chain = GeocoderChain([OpenMeteoGeocoder(http), PhotonGeocoder(http)])
    with pytest.raises(GeocodeNotFound):
        chain.geocode("qzxvjkqpwzxvnotaplace")


# ---------------------------------------------------------------------------
# 2. Routing
# ---------------------------------------------------------------------------


def test_live_osrm_returns_usable_alternatives(http: HttpClient) -> None:
    """Live routing yields real geometry with sane distances."""
    router = OSRMRouter(http)
    routes = router.route(PLACE, (42.3314, -83.0458))

    assert len(routes) >= 1, "OSRM returned no route for a 280 mi corridor"
    for route in routes:
        assert len(route.geometry) >= 2, "a route needs at least two points"
        for lon, lat in route.geometry:
            assert -180 <= lon <= 180, "longitude out of range"
            assert -90 <= lat <= 90, "latitude out of range"
        assert route.distance_m > 0
        assert route.duration_s > 0
        assert route.source.startswith("osrm"), f"unexpected source {route.source!r}"

    # A driving distance that is plausible for this corridor. The generous
    # bounds catch a unit mix-up (metres vs miles) without failing when OSRM
    # routes a detour.
    miles = [r.distance_m / 1609.344 for r in routes]
    assert all(150 < m < 600 for m in miles), f"implausible distances: {miles}"

    # The geometries must actually differ, or "three alternatives" is a lie.
    if len(routes) > 1:
        assert (
            len({tuple(r.geometry[0]) for r in routes}) > 1
            or len({len(r.geometry) for r in routes}) > 1
        )


# ---------------------------------------------------------------------------
# 3. Weather
# ---------------------------------------------------------------------------


def test_live_weather_returns_aligned_series_in_documented_units(
    http: HttpClient,
) -> None:
    """Live weather parses, and the units are the ones the rules assume.

    The snow assertion is the important one. `snowfall` from Open-Meteo is snow
    *depth* while `precipitation` is liquid equivalent -- roughly 7:1 (D15). If a
    provider ever changed that, every snow threshold in the app would be wrong
    by a factor of seven, and nothing offline could detect it.
    """
    provider = OpenMeteoWeatherProvider(http)
    departure = (datetime.now(UTC) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
    window: ForecastWindow = plan_window(departure, longest_route_hours=8, slider_hours=48)

    locations = [PLACE, (42.3314, -83.0458), (41.4993, -81.6944)]
    series = provider.fetch(locations, window=window)

    assert len(series) == len(locations), "one series per location, in order"
    lengths = {len(s.times) for s in series}
    assert len(lengths) == 1, f"every location shares one time axis, got {lengths}"
    hours = lengths.pop()

    # A superset, not an equality. The provider is asked for whole days
    # (`forecast_days`), so it returns whole days; the contract is that the
    # series *covers* the window, because `build_hourly_risk` indexes into it
    # with `window.index_of`. A series shorter than the window would put every
    # offset hour past the end of the array.
    assert hours >= window.hours, (
        f"series of {hours} hours does not cover the {window.hours}-hour window"
    )

    for s in series:
        assert all(t.tzinfo is not None for t in s.times), "times must be timezone-aware"
        # `times` is a tuple, so it must be compared as a sequence -- comparing
        # a tuple to the list `sorted()` returns is always False.
        assert list(s.times) == sorted(s.times), "hours must ascend"
        assert len(set(s.times)) == len(s.times), "hours must be unique"
        assert s.times[0] <= window.start, "series must begin no later than the window"
        assert s.times[-1] >= window.end, "series must reach the end of the window"
        # One index of shared axis: every location's hour i is the same instant.
        break

    first_times = series[0].times
    for s in series[1:]:
        assert s.times == first_times, "locations must be aligned on a common axis"
        # Physically plausible ranges. A provider returning 500 mph or a
        # negative temperature-equivalent reading is reporting a unit change.
        assert all(0 <= w <= 250 for w in s.wind_mph), f"wind out of range: {s.wind_mph[:6]}"
        assert all(0 <= r <= 20 for r in s.rain_in_hr), f"rain out of range: {s.rain_in_hr[:6]}"
        assert all(0 <= sn <= 20 for sn in s.snow_in_hr), f"snow out of range: {s.snow_in_hr[:6]}"

    # The arrays must be parallel: reading hour i of each gives the same hour.
    reference = series[0]
    for s in series:
        for name in ("wind_mph", "rain_in_hr", "snow_in_hr", "precipitation_in_hr"):
            values = getattr(s, name)
            assert len(values) == len(reference.times), f"{name} is not aligned with times"


def test_live_snow_is_depth_not_liquid_equivalent(http: HttpClient) -> None:
    """Where both are non-zero, snow depth must exceed the liquid total.

    Asserted on a *live* series, which is the only place the claim can hold: a
    recorded fixture would only prove it was true when it was recorded. The test
    skips rather than fails when there is no snow on the route -- asserting a
    ratio with a zero numerator is how a test becomes meaningless.
    """
    provider = OpenMeteoWeatherProvider(http)
    departure = (datetime.now(UTC) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
    window = plan_window(departure, longest_route_hours=48, slider_hours=48)

    # A winter latitude, so snow is actually plausible.
    series = provider.fetch([(44.98, -93.27), (41.58, -93.62)], window=window)

    checked = 0
    for s in series:
        for depth, liquid in zip(s.snow_in_hr, s.precipitation_in_hr, strict=True):
            if depth > 0.05 and liquid > 0.0:
                checked += 1
                # Snow depth in inches vs liquid inches: depth is larger. The
                # bound is loose because a provider may report an equivalent
                # for some hours; the point is catching a wholesale swap.
                assert depth > liquid * 1.5, (
                    f"snow depth {depth} in/hr is not clearly above the liquid "
                    f"equivalent {liquid} in/hr -- is `snowfall` now liquid?"
                )
    if checked == 0:
        pytest.skip("no snow in the current forecast window; ratio not checkable")


# ---------------------------------------------------------------------------
# 4. The whole pipeline
# ---------------------------------------------------------------------------


def test_live_end_to_end_analysis_is_self_consistent(http: HttpClient) -> None:
    """A full live analysis produces a payload that holds together.

    This is the test that would catch an upstream change that breaks
    composition: each provider answering correctly on its own while the
    combination no longer produces three routes.
    """
    registry = ServiceRegistry(
        geocoder=GeocoderChain([OpenMeteoGeocoder(http), PhotonGeocoder(http)]),
        router=OSRMRouter(http),
        weather=OpenMeteoWeatherProvider(http),
    )
    service = TripAnalysisService(registry)
    departure = (datetime.now(UTC) + timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)

    analysis = service.analyze(
        TripQuery(
            origin=ORIGIN,
            destination=DESTINATION,
            departure=departure,
            load_lb=40_000,
            spacing_mi=50,
            slider_hours=48,
        )
    )
    payload = analysis.payload()

    assert len(payload["routes"]) == 3, "three options is a product guarantee"
    for route in payload["routes"]:
        assert len(route["geometry"]) >= 2
        summary = route["summary"]
        # Mileage must partition the route (D4). A rounding slip upstream would
        # show here rather than as a ranking nobody can explain.
        total = sum(summary["miles_by_level"].values())
        assert abs(total - summary["total_miles"]) < 0.5, (
            f"{route['id']}: mileage does not partition the route "
            f"({total} vs {summary['total_miles']})"
        )
        assert 1.0 <= summary["avg_risk"] <= 5.0
        assert summary["worst_level"] in {"LOW", "MODERATE", "HIGH", "SEVERE", "NO_TRAVEL"}

    for bundle in payload["checkpoints"]:
        for point in bundle["points"]:
            levels = point["hourly_risk"]
            assert levels, "a checkpoint with no hourly risk cannot be read"
            index = point["eta_hour_index"]
            assert 0 <= index < len(levels), "eta index outside the hourly array"
            # The slider reads this far ahead, so the array must reach it.
            assert index + payload["meta"]["slider_hours"] <= len(levels), (
                "hourly array is too short for the departure slider"
            )

    recommendation = payload["recommendation"]
    assert recommendation["route_id"] in {r["id"] for r in payload["routes"]}
    assert len(recommendation["ranking"]) == len(payload["routes"])


# ---------------------------------------------------------------------------
# 5. Latency recording
# ---------------------------------------------------------------------------


def test_record_live_latencies(http: HttpClient) -> None:
    """Time each live leg and write them where the benchmark writer can find them.

    Latencies are *recorded*, never asserted against a threshold. A hard budget
    in a test turns a third party's slow day into a red build for everyone, and
    the team ends up raising the number instead of investigating. The budget
    lives in the docs; this only measures it.

    Writes to ``$TRIPPLAN_BENCH_OUT`` when set (which is how
    ``make bench-integration`` records them); otherwise prints and drops.
    """
    timings: dict[str, list[float]] = {}

    chain = GeocoderChain([OpenMeteoGeocoder(http), PhotonGeocoder(http)])
    timed("geocode", timings, lambda: chain.geocode(ORIGIN))
    timed("geocode", timings, lambda: chain.geocode(DESTINATION))

    router = OSRMRouter(http)
    routes = timed("route", timings, lambda: router.route(PLACE, (42.3314, -83.0458)))

    provider = OpenMeteoWeatherProvider(http)
    departure = (datetime.now(UTC) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
    window = plan_window(departure, longest_route_hours=8, slider_hours=48)
    series = timed(
        "weather",
        timings,
        lambda: provider.fetch([PLACE, (42.3314, -83.0458)], window=window),
    )

    report = {
        label: {
            "samples": len(samples),
            "median_s": round(statistics.median(samples), 3),
            "max_s": round(max(samples), 3),
        }
        for label, samples in timings.items()
    }
    report["_notes"] = {
        "routes_returned": len(routes),
        "locations": len(series),
        "window_hours": window.hours,
    }

    print(f"\nlive latencies: {json.dumps(report, indent=2)}")

    # An explicit env var rather than a pytest hook: the writer is a separate
    # step (`make bench-integration`) that appends to docs/BENCHMARKS.md, and a
    # file handoff is easier to reason about than a plugin mutating the repo
    # from inside a test.
    destination = os.environ.get("TRIPPLAN_BENCH_OUT")
    if destination:
        payload = dict(report)
        payload["recorded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        Path(destination).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {destination}")
    else:
        print("(TRIPPLAN_BENCH_OUT unset; run `make bench-integration` to record)")
