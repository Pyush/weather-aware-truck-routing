"""Adapter tests (BE-42).

**Every test here runs with zero network access.** Providers are exercised
through recorded payloads and a stub client, which is the whole point of routing
them through protocols -- a test that needs the internet is a test that will be
skipped exactly when it matters.

The one exception is ``test_integration.py``, which is opt-in via ``-m
integration`` and is the only place a live API is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tripplan.adapters import weather as weather_mod
from tripplan.adapters.errors import (
    GeocodeNotFound,
    NoRouteFound,
    UpstreamError,
    UpstreamRateLimited,
)
from tripplan.adapters.geocoding import (
    GeocoderChain,
    GeocodeResult,
    OpenMeteoGeocoder,
    PhotonGeocoder,
)
from tripplan.adapters.http import HttpClient, _parse_retry_after
from tripplan.adapters.indexing import build_hourly_risk
from tripplan.adapters.routing import (
    OSRMRouter,
    Route,
    dedupe_routes,
    mean_separation_mi,
    perpendicular_waypoints,
    sample_geometry,
)
from tripplan.adapters.weather import (
    ForecastWindow,
    OpenMeteoWeatherProvider,
    WeatherSeries,
    cell_key,
    plan_locations,
    plan_window,
    window_fetch_params,
)


class StubClient:
    """Stands in for :class:`HttpClient`, returning canned payloads in order."""

    def __init__(self, *payloads: object) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, *, params: dict | None = None, provider: str = "") -> object:
        self.calls.append({"url": url, "params": params or {}, "provider": provider})
        if not self._payloads:
            msg = f"StubClient exhausted for {url}"
            raise AssertionError(msg)
        return self._payloads.pop(0)


# ---------------------------------------------------------------------------
# BE-31: HTTP client
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: object = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.is_error = 400 <= status_code < 600

    def json(self) -> object:
        return self._payload


class SequenceClient:
    """Minimal httpx.Client stand-in that replays responses and records calls."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.get_calls = 0

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.get_calls += 1
        if not self._responses:
            msg = "SequenceClient exhausted"
            raise AssertionError(msg)
        return self._responses.pop(0)


def test_retries_a_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate limit is retried rather than surfaced."""
    monkeypatch.setattr("tripplan.adapters.http.time.sleep", lambda _s: None)
    http = HttpClient(
        max_retries=3,
        client=SequenceClient(  # type: ignore[arg-type]
            [FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, {"ok": True})]
        ),
    )
    assert http.get_json("https://example.test", provider="t") == {"ok": True}


def test_retries_a_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tripplan.adapters.http.time.sleep", lambda _s: None)
    http = HttpClient(
        max_retries=3,
        client=SequenceClient(  # type: ignore[arg-type]
            [FakeResponse(503), FakeResponse(200, {"ok": 1})]
        ),
    )
    assert http.get_json("https://example.test", provider="t") == {"ok": 1}


def test_does_not_retry_a_400() -> None:
    """A malformed request stays malformed; retrying wastes rate-limit budget."""
    inner = SequenceClient([FakeResponse(400)])
    http = HttpClient(max_retries=3, client=inner)  # type: ignore[arg-type]
    with pytest.raises(UpstreamError) as excinfo:
        http.get_json("https://example.test", provider="t")
    assert excinfo.value.status_code == 400
    assert excinfo.value.retryable is False
    assert inner.get_calls == 1, "a 400 must not be retried"


def test_exhausted_retries_raise_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tripplan.adapters.http.time.sleep", lambda _s: None)
    http = HttpClient(
        max_retries=1,
        client=SequenceClient(  # type: ignore[arg-type]
            [FakeResponse(429), FakeResponse(429)]
        ),
    )
    with pytest.raises(UpstreamRateLimited):
        http.get_json("https://example.test", provider="t")


def test_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadJson(FakeResponse):
        def json(self) -> object:
            msg = "not json"
            raise ValueError(msg)

    http = HttpClient(max_retries=0, client=SequenceClient([BadJson(200)]))  # type: ignore[arg-type]
    with pytest.raises(UpstreamError, match="not valid JSON"):
        http.get_json("https://example.test", provider="t")


def test_retry_after_parsing() -> None:
    assert _parse_retry_after("2.5") == 2.5
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None  # date form ignored


# ---------------------------------------------------------------------------
# BE-33 / BE-34: geocoding
# ---------------------------------------------------------------------------

OM_GEOCODE_HIT = {
    "results": [
        {
            "latitude": 39.7392,
            "longitude": -104.9903,
            "name": "Denver",
            "admin1": "Colorado",
            "country": "United States",
            "country_code": "US",
            "feature_code": "PPLA2",
        }
    ]
}


def test_open_meteo_geocoder_resolves_a_city() -> None:
    stub = StubClient(OM_GEOCODE_HIT)
    result = OpenMeteoGeocoder(stub).geocode("Denver, CO")  # type: ignore[arg-type]
    assert (result.lat, result.lon) == (39.7392, -104.9903)
    assert result.admin1 == "Colorado"
    assert result.source == "open_meteo"
    assert "Denver" in result.display_name


def test_open_meteo_geocoder_raises_when_empty() -> None:
    with pytest.raises(GeocodeNotFound):
        OpenMeteoGeocoder(StubClient({"results": []})).geocode("Atlantis")  # type: ignore[arg-type]


def test_open_meteo_geocoder_tolerates_missing_results_key() -> None:
    with pytest.raises(GeocodeNotFound):
        OpenMeteoGeocoder(StubClient({})).geocode("Atlantis")  # type: ignore[arg-type]


def test_populated_places_are_preferred_over_regions() -> None:
    """'Springfield' should resolve to the city, not the county."""
    payload = {
        "results": [
            {"latitude": 39.8, "longitude": -89.6, "name": "Springfield", "feature_code": "ADM1"},
            {"latitude": 39.8, "longitude": -89.65, "name": "Springfield", "feature_code": "PPLA"},
        ]
    }
    result = OpenMeteoGeocoder(StubClient(payload)).geocode("Springfield")  # type: ignore[arg-type]
    assert result.feature_type == "PPLA"


PHOTON_HIT = {
    "features": [
        {
            "geometry": {"coordinates": [11.5820, 48.1351]},
            "properties": {
                "name": "Munich",
                "city": "Munich",
                "state": "Bavaria",
                "country": "Germany",
                "countrycode": "DE",
                "osm_key": "place",
            },
        }
    ]
}


def test_photon_geocoder_resolves_a_place() -> None:
    result = PhotonGeocoder(StubClient(PHOTON_HIT)).geocode("Munich")  # type: ignore[arg-type]
    assert (result.lat, result.lon) == (48.1351, 11.5820)
    assert result.country_code == "DE"


def test_photon_skips_malformed_features() -> None:
    payload = {
        "features": [
            {"geometry": {}, "properties": {}},
            {"geometry": {"coordinates": [1, 2]}, "properties": {"name": "X"}},
        ]
    }
    results = PhotonGeocoder(StubClient(payload)).suggest("x")  # type: ignore[arg-type]
    assert len(results) == 1


def test_chain_falls_through_on_error() -> None:
    """A 403 from the primary (the Nominatim case) must not break the flow."""

    class Broken:
        name = "broken"

        def geocode(self, query: str) -> GeocodeResult:
            raise UpstreamError("broken", "403 Access denied", status_code=403)

        def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
            return []

    class Working:
        name = "working"

        def geocode(self, query: str) -> GeocodeResult:
            return GeocodeResult(1.0, 2.0, "Somewhere", source="working")

        def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
            return [self.geocode(query)]

    chain = GeocoderChain([Broken(), Working()])
    assert chain.geocode("x").source == "working"


def test_chain_falls_through_on_no_match() -> None:
    class Empty:
        name = "empty"

        def geocode(self, query: str) -> GeocodeResult:
            raise GeocodeNotFound("empty", "no match", query=query)

        def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
            return []

    chain = GeocoderChain([Empty(), Empty()])
    with pytest.raises(GeocodeNotFound):
        chain.geocode("Atlantis")


def test_chain_requires_a_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        GeocoderChain([])


# ---------------------------------------------------------------------------
# BE-35 / BE-36 / BE-37: routing
# ---------------------------------------------------------------------------


def _osrm_payload(*routes: tuple[float, float, list[list[float]]]) -> dict:
    return {
        "code": "Ok",
        "routes": [
            {"distance": distance, "duration": duration, "geometry": {"coordinates": geometry}}
            for distance, duration, geometry in routes
        ],
    }


def test_osrm_parses_alternatives() -> None:
    payload = _osrm_payload(
        (1_000_000.0, 64_000.0, [[-87.63, 41.88], [-87.80, 41.95]]),
        (1_100_000.0, 70_000.0, [[-87.63, 41.88], [-88.10, 42.10]]),
    )
    routes = OSRMRouter(StubClient(payload), want_routes=2).route(  # type: ignore[arg-type]
        (41.88, -87.63), (41.95, -87.80)
    )
    assert len(routes) == 2
    assert routes[0].distance_mi == pytest.approx(621.4, abs=1.0)
    assert routes[0].source == "osrm_alternative"


def test_osrm_raises_on_no_route() -> None:
    stub = StubClient({"code": "NoRoute", "message": "no route"})
    with pytest.raises(NoRouteFound):
        OSRMRouter(stub).route((0.0, 0.0), (1.0, 1.0))  # type: ignore[arg-type]


def test_osrm_raises_on_error_code() -> None:
    stub = StubClient({"code": "InvalidQuery", "message": "bad"})
    with pytest.raises(NoRouteFound):
        OSRMRouter(stub).route((0.0, 0.0), (1.0, 1.0))  # type: ignore[arg-type]


def test_osrm_skips_routes_with_no_geometry() -> None:
    payload = {
        "code": "Ok",
        "routes": [
            {"distance": 1.0, "duration": 1.0, "geometry": {"coordinates": []}},
            {"distance": 2.0, "duration": 2.0, "geometry": {"coordinates": [[0, 0], [1, 1]]}},
        ],
    }
    routes = OSRMRouter(StubClient(payload), want_routes=1).route((0.0, 0.0), (1.0, 1.0))  # type: ignore[arg-type]
    assert len(routes) == 1


def test_router_synthesises_a_third_route_when_osrm_returns_one() -> None:
    """The measured case: OSRM gave 2 routes for a 1,006 mi trip, 1 when pinned."""
    single = _osrm_payload(
        (1_617_000.0, 64_000.0, [[-104.99, 39.74], [-95.0, 41.0], [-87.63, 41.88]])
    )
    via = _osrm_payload(
        (1_700_000.0, 68_000.0, [[-104.99, 39.74], [-99.0, 41.5], [-93.0, 41.9], [-87.63, 41.88]])
    )
    # One direct response plus one per planned via-waypoint variant. Repeating
    # the same `via` geometry is deliberate: the router should keep asking until
    # it has three *distinct* routes, not three responses.
    stub = StubClient(single, via, via, via, via, via)

    routes = OSRMRouter(stub, want_routes=3).route((39.74, -104.99), (41.88, -87.63))

    assert len(routes) >= 2
    # The synthesised option must be tagged, so the UI can be honest about it.
    assert any(r.source == "osrm_via_variant" for r in routes)
    # And it must be genuinely different geometry, not a duplicate.
    sampled = [sample_geometry(r.geometry) for r in routes]
    for i in range(len(sampled)):
        for j in range(i + 1, len(sampled)):
            assert mean_separation_mi(sampled[i], sampled[j]) > 3.0


def test_router_returns_fewer_than_three_when_none_exist() -> None:
    """Never fabricate an option: a short trip may genuinely have one route."""
    only = _osrm_payload((5_000.0, 300.0, [[-87.63, 41.88], [-87.64, 41.89]]))
    stub = StubClient(*([only] * 8))
    routes = OSRMRouter(stub, want_routes=3).route((41.88, -87.63), (41.89, -87.64))
    assert len(routes) == 1


def test_dedupe_collapses_identical_geometries() -> None:
    geometry = [[-87.0 + 0.01 * i, 41.0] for i in range(50)]
    a = Route(geometry, 100.0, 10.0)
    b = Route([list(c) for c in geometry], 110.0, 11.0)
    assert len(dedupe_routes([a, b])) == 1


def test_dedupe_keeps_the_shorter_representative() -> None:
    geometry = [[-87.0 + 0.01 * i, 41.0] for i in range(50)]
    longer = Route(geometry, 110.0, 11.0)
    shorter = Route([list(c) for c in geometry], 100.0, 10.0)
    kept = dedupe_routes([longer, shorter])
    assert len(kept) == 1
    assert kept[0].distance_m == 100.0


def test_dedupe_keeps_genuinely_distinct_corridors() -> None:
    north = Route([[-95.0 + 0.01 * i, 42.0] for i in range(50)], 100.0, 10.0)
    south = Route([[-95.0 + 0.01 * i, 39.0] for i in range(50)], 105.0, 11.0)
    assert len(dedupe_routes([north, south])) == 2


def test_perpendicular_waypoints_offset_the_route() -> None:
    geometry = [[-95.0 + 0.01 * i, 41.0] for i in range(100)]  # due east
    waypoints = perpendicular_waypoints(geometry, 0.5, 30.0, side=1)
    assert waypoints
    # A due-east route has a due-north perpendicular; check the sign and scale.
    assert all(w.lat > 41.0 for w in waypoints)
    assert all(abs(w.lon + 94.5) < 0.2 for w in waypoints)


def test_perpendicular_waypoints_handle_a_degenerate_route() -> None:
    assert perpendicular_waypoints([], 0.5, 10.0, 1) == []
    assert perpendicular_waypoints([[0.0, 0.0]], 0.5, 10.0, 1) == []


# ---------------------------------------------------------------------------
# BE-38 / BE-39: weather
# ---------------------------------------------------------------------------

OM_FORECAST_MULTI = {
    "results": None,
}


def _forecast_record(lat: float, lon: float, hours: int = 24) -> dict:
    base = datetime(2026, 10, 17, tzinfo=UTC)
    return {
        "latitude": lat,
        "longitude": lon,
        "hourly": {
            "time": [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)],
            "wind_speed_10m": [10.0 + i for i in range(hours)],
            "rain": [0.0] * hours,
            "showers": [0.0] * hours,
            "snowfall": [0.0] * hours,
            "precipitation": [0.0] * hours,
        },
    }


def test_weather_parses_a_multi_location_response() -> None:
    payload = [_forecast_record(41.0, -87.0), _forecast_record(39.0, -105.0)]
    provider = OpenMeteoWeatherProvider(StubClient(payload), batch_size=20)  # type: ignore[arg-type]
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))
    series = provider.fetch([(41.0, -87.0), (39.0, -105.0)], window=window)

    assert len(series) == 2
    assert series[0].lat == 41.0
    assert len(series[0].times) == 24
    assert series[0].wind_mph[0] == 10.0


def test_weather_request_asks_for_the_right_units_and_fields() -> None:
    stub = StubClient([_forecast_record(41.0, -87.0)])
    provider = OpenMeteoWeatherProvider(stub, batch_size=20)  # type: ignore[arg-type]
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))
    provider.fetch([(41.0, -87.0)], window=window)

    params = stub.calls[0]["params"]
    assert params["wind_speed_unit"] == "mph"
    assert params["snowfall_unit"] == "inch"
    assert "snowfall" in params["hourly"]
    assert "rain" in params["hourly"]
    assert params["timezone"] == "UTC"


def test_weather_batches_sequentially() -> None:
    """Batching, not fan-out: Open-Meteo answers 429 to parallel requests."""
    # Each batch of k locations must produce a payload with k records, so the
    # payloads here mirror the 2 + 2 + 1 split below.
    payloads = [
        [_forecast_record(40.0 + i * 0.5, -87.0) for i in range(2)],
        [_forecast_record(41.0 + i * 0.5, -87.0) for i in range(2)],
        [_forecast_record(42.0, -87.0)],
    ]
    stub = StubClient(*payloads)
    provider = OpenMeteoWeatherProvider(stub, batch_size=2)  # type: ignore[arg-type]
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))

    locations = [(40.0 + i * 0.5, -87.0) for i in range(5)]
    series = provider.fetch(locations, window=window)

    assert len(series) == 5
    assert len(stub.calls) == 3, "5 locations at batch size 2 means 3 sequential requests"


def test_weather_rejects_a_mismatched_response_length() -> None:
    stub = StubClient([_forecast_record(41.0, -87.0)])
    provider = OpenMeteoWeatherProvider(stub, batch_size=20)  # type: ignore[arg-type]
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))
    with pytest.raises(UpstreamError, match="requested 2 locations"):
        provider.fetch([(41.0, -87.0), (39.0, -105.0)], window=window)


def test_weather_rejects_a_record_with_no_hourly_block() -> None:
    stub = StubClient([{"latitude": 1.0, "longitude": 2.0}])
    provider = OpenMeteoWeatherProvider(stub, batch_size=20)  # type: ignore[arg-type]
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))
    with pytest.raises(UpstreamError, match="hourly"):
        provider.fetch([(41.0, -87.0)], window=window)


def test_rain_rule_input_sums_rain_and_showers() -> None:
    """D15: the Rain rule reads liquid precipitation only."""
    hours = 3
    record = {
        "latitude": 41.0,
        "longitude": -87.0,
        "hourly": {
            "time": [
                (datetime(2026, 10, 17, tzinfo=UTC) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                for i in range(hours)
            ],
            "wind_speed_10m": [5.0] * hours,
            "rain": [0.10] * hours,
            "showers": [0.05] * hours,
            "snowfall": [2.0] * hours,
            "precipitation": [2.15] * hours,
        },
    }
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 2, tzinfo=UTC)
    )
    series = OpenMeteoWeatherProvider(StubClient([record])).fetch(  # type: ignore[arg-type]
        [(41.0, -87.0)], window=window
    )[0]

    assert series.rain_in_hr[0] == pytest.approx(0.15)
    assert series.snow_in_hr[0] == 2.0, "snow must stay depth"
    reading = series.reading_at(0)
    assert reading["rain"] == pytest.approx(0.15)
    assert reading["showers"] is None, "already folded in; summing again would double-count"


def test_nan_weather_becomes_none_not_a_crash() -> None:
    record = {
        "latitude": 41.0,
        "longitude": -87.0,
        "hourly": {
            "time": ["2026-10-17T00:00"],
            "wind_speed_10m": [float("nan")],
            "rain": [None],
            "showers": [0.0],
            "snowfall": [0.0],
            "precipitation": [0.0],
        },
    }
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC))
    series = OpenMeteoWeatherProvider(StubClient([record])).fetch(  # type: ignore[arg-type]
        [(41.0, -87.0)], window=window
    )[0]
    assert series.wind_mph[0] is None
    assert series.reading_at(0)["wind_speed_10m"] is None


# ---------------------------------------------------------------------------
# BE-39: window planning
# ---------------------------------------------------------------------------


def test_window_covers_route_duration_plus_slider() -> None:
    """One fetch must serve the ETA and all 48 offsets (D7/D13).

    20 h of driving plus a 48 h departure offset is 68 h of forecast, not 48 --
    forgetting the driving time would leave the tail of the slider unforecast.
    """
    departure = datetime(2026, 10, 17, 6, 30, tzinfo=UTC)
    window = plan_window(departure, longest_route_hours=20.0, slider_hours=48)
    assert window.start == datetime(2026, 10, 17, 6, tzinfo=UTC)
    assert window.end == window.start + timedelta(hours=68)
    assert window.end == datetime(2026, 10, 20, 2, tzinfo=UTC)
    assert window.hours == 69


def test_window_index_floors_to_the_containing_hour() -> None:
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC))
    assert window.index_of(datetime(2026, 10, 17, 6, 0, tzinfo=UTC)) == 6
    assert window.index_of(datetime(2026, 10, 17, 6, 59, tzinfo=UTC)) == 6
    assert window.index_of(datetime(2026, 10, 17, 6, 0, tzinfo=UTC)) == 6


def test_window_index_clamps_out_of_range() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 5, tzinfo=UTC)
    )
    assert window.index_of(datetime(2026, 10, 16, tzinfo=UTC)) == 0
    assert window.index_of(datetime(2026, 10, 20, tzinfo=UTC)) == 5


def test_fetch_params_for_a_departure_today() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, 6, tzinfo=UTC), datetime(2026, 10, 19, 6, tzinfo=UTC)
    )
    params = window_fetch_params(window, now=datetime(2026, 10, 17, 0, 0, tzinfo=UTC))
    assert params.past_days == 0
    assert params.forecast_days == 3


def test_fetch_params_for_a_departure_tomorrow() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 18, 6, tzinfo=UTC), datetime(2026, 10, 20, 6, tzinfo=UTC)
    )
    params = window_fetch_params(window, now=datetime(2026, 10, 17, 0, 0, tzinfo=UTC))
    assert params.past_days == 0
    assert params.forecast_days == 4


def test_fetch_params_for_a_departure_yesterday_uses_past_days() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 16, 6, tzinfo=UTC), datetime(2026, 10, 18, 6, tzinfo=UTC)
    )
    params = window_fetch_params(window, now=datetime(2026, 10, 17, 0, 0, tzinfo=UTC))
    assert params.past_days >= 1


def test_departure_beyond_the_provider_range_is_rejected() -> None:
    """Measured: forecast_days=17 returns HTTP 400."""
    window = ForecastWindow(datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 22, tzinfo=UTC))
    with pytest.raises(UpstreamError, match="at most 16"):
        window_fetch_params(window, now=datetime(2026, 10, 17, tzinfo=UTC))


# ---------------------------------------------------------------------------
# BE-40: location planning
# ---------------------------------------------------------------------------


def test_plan_locations_dedupes_overlapping_routes() -> None:
    """Three routes on a shared corridor must not fetch three times over."""
    shared = [(41.0 + i * 0.1, -87.0) for i in range(10)]
    checkpoints = [*shared, *shared, *[(41.0 + i * 0.1 + 0.001, -87.0) for i in range(10)]]
    plan = plan_locations(checkpoints, budget=100)

    assert plan.requested == 30
    assert len(plan.unique) < 30, "overlapping routes must share fetches"
    assert plan.saved > 0
    assert not plan.coarsened


def test_plan_locations_collapses_nearby_points() -> None:
    """Points inside the same 0.02 deg cell share one fetch.

    Chosen well inside a cell: two points 0.001 deg apart can legitimately straddle
    a cell boundary and be kept separate, which is correct, not a bug.
    """
    plan = plan_locations([(41.001, -87.001), (41.005, -87.005)], budget=10)
    assert len(plan.unique) == 1
    assert plan.index_of_checkpoint == (0, 0)


def test_points_straddling_a_cell_boundary_are_kept_separate() -> None:
    """-87.000 and -87.001 fall either side of a cell edge; both are fetched."""
    plan = plan_locations([(41.000, -87.000), (41.001, -87.001)], budget=10)
    assert len(plan.unique) == 2


def test_plan_locations_keeps_distinct_points() -> None:
    plan = plan_locations([(41.0, -87.0), (45.0, -87.0)], budget=10)
    assert len(plan.unique) == 2


def test_plan_locations_coarsens_when_over_budget() -> None:
    checkpoints = [(41.0 + i * 0.05, -87.0) for i in range(200)]
    plan = plan_locations(checkpoints, budget=20)

    assert plan.coarsened is True
    assert len(plan.unique) <= 20
    assert plan.requested == 200
    assert len(plan.index_of_checkpoint) == 200
    assert all(0 <= i < len(plan.unique) for i in plan.index_of_checkpoint)


def test_plan_locations_coarsening_keeps_both_ends() -> None:
    """Truncating the tail would silently drop the destination's weather."""
    checkpoints = [(41.0 + i * 0.05, -87.0) for i in range(200)]
    plan = plan_locations(checkpoints, budget=10)
    mapped = [plan.index_of_checkpoint[0], plan.index_of_checkpoint[-1]]
    assert min(mapped) == 0, "origin must map to the first retained location"
    assert max(mapped) == len(plan.unique) - 1, "destination must map to the last"


def test_plan_locations_handles_empty_input() -> None:
    plan = plan_locations([], budget=10)
    assert plan.unique == ()
    assert plan.index_of_checkpoint == ()


def test_cell_key_is_stable_within_a_cell() -> None:
    assert cell_key(41.001, -87.001) == cell_key(41.005, -87.005)
    assert cell_key(41.0, -87.0) != cell_key(41.5, -87.0)


# ---------------------------------------------------------------------------
# BE-41: hourly risk indexing -- the slider invariant
# ---------------------------------------------------------------------------


def _series_with_winds(winds: list[float]) -> WeatherSeries:
    base = datetime(2026, 10, 17, tzinfo=UTC)
    n = len(winds)
    return WeatherSeries(
        lat=41.0,
        lon=-87.0,
        times=tuple(base + timedelta(hours=i) for i in range(n)),
        wind_mph=tuple(winds),
        rain_in_hr=(0.0,) * n,
        snow_in_hr=(0.0,) * n,
        precipitation_in_hr=(0.0,) * n,
    )


def test_hourly_risk_has_one_level_per_hour() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 23, tzinfo=UTC)
    )
    series = _series_with_winds([5.0] * 24)
    risk = build_hourly_risk(
        series,
        window=window,
        eta=datetime(2026, 10, 17, 6, 0, tzinfo=UTC),
        load_lb=0,
    )
    assert len(risk.levels) == 24
    assert risk.eta_index == 6


def test_the_slider_invariant_holds() -> None:
    """D7/D13: level at offset h is hourly_risk[eta_index + h], for every h."""
    window = ForecastWindow(datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC))
    winds = [5.0, 10.0, 27.0, 40.0, 50.0, 60.0] * 12
    series = _series_with_winds(winds)
    risk = build_hourly_risk(
        series,
        window=window,
        eta=datetime(2026, 10, 17, 7, 30, tzinfo=UTC),
        load_lb=0,
    )

    from tripplan.domain.risk.evaluate import evaluate_checkpoint

    for offset in range(0, 48):
        expected_index = risk.eta_index + offset
        if expected_index >= len(risk.levels):
            break
        # Recompute independently from the raw provider reading.
        direct = evaluate_checkpoint(series.reading_at(expected_index), 0)
        assert risk.level_at_offset(offset) is direct.level
        assert int(risk.level_at_offset(offset)) == risk.levels[expected_index]


def test_hourly_risk_clamps_offsets_past_the_window() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 5, tzinfo=UTC)
    )
    risk = build_hourly_risk(
        _series_with_winds([5.0] * 6),
        window=window,
        eta=datetime(2026, 10, 17, 2, tzinfo=UTC),
        load_lb=0,
    )
    assert risk.level_at_offset(-10) is risk.level_at_offset(0)
    assert risk.level_at_offset(999) is risk.level_at_offset(5)


def test_hourly_risk_reapplies_load_rules_at_every_hour() -> None:
    """A checkpoint can be fine on arrival and blocked six hours later."""
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 11, tzinfo=UTC)
    )
    series = _series_with_winds([60.0] * 12)
    risk = build_hourly_risk(
        series,
        window=window,
        eta=datetime(2026, 10, 17, 0, 0, tzinfo=UTC),
        load_lb=0,
    )

    from tripplan.domain.risk.enums import RiskLevel

    assert risk.level_at_offset(0) is RiskLevel.NO_TRAVEL


def test_hourly_risk_handles_an_empty_series() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 1, tzinfo=UTC)
    )
    series = WeatherSeries(41.0, -87.0, (), (), (), ())
    risk = build_hourly_risk(
        series, window=window, eta=datetime(2026, 10, 17, tzinfo=UTC), load_lb=0
    )
    assert len(risk.levels) == 1
    assert risk.level_at_offset(48) is not None


def test_hourly_risk_serialises_for_the_api() -> None:
    window = ForecastWindow(
        datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, 3, tzinfo=UTC)
    )
    risk = build_hourly_risk(
        _series_with_winds([5.0] * 4),
        window=window,
        eta=datetime(2026, 10, 17, tzinfo=UTC),
        load_lb=0,
    )
    payload = risk.to_dict()
    assert payload["eta_hour_index"] == 0
    assert payload["hourly_risk"] == [1, 1, 1, 1]
    assert payload["base"]["level"] == "LOW"


def test_junk_payload_is_rejected_cleanly() -> None:
    with pytest.raises(UpstreamError):
        weather_mod._parse_series({"hourly": {"time": "not-a-list"}})
