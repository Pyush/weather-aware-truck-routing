"""API contract tests (BE-59).

These run entirely against the deterministic mock provider, so the suite never
spends a third party's rate-limit budget and never flakes on an upstream
outage. What they protect is the *contract*: event order, payload shape, the
``hourly_risk`` alignment the slider depends on, and the error taxonomy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from tripplan.adapters.geocoding import GeocodeResult
from tripplan.adapters.mock_weather import MockWeatherProvider
from tripplan.adapters.routing import Route
from tripplan.api import views
from tripplan.services.trip_analysis import ServiceRegistry, TripAnalysisService

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class StubGeocoder:
    name = "stub"

    PLACES = {
        "chicago, il": GeocodeResult(41.8781, -87.6298, "Chicago, IL", "US", "Illinois"),
        "detroit, mi": GeocodeResult(42.3314, -83.0458, "Detroit, MI", "US", "Michigan"),
    }

    def geocode(self, query: str) -> GeocodeResult:
        """Resolve a known place."""
        from tripplan.adapters.errors import GeocodeNotFound

        key = query.strip().lower()
        if key not in self.PLACES:
            raise GeocodeNotFound(self.name, f"no match for {query!r}", query=query)
        return self.PLACES[key]

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Candidates for a query."""
        return [self.geocode(query)]


class StubRouter:
    name = "stub"

    def __init__(self, routes: int = 3) -> None:
        self._count = routes

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> list[Route]:
        """Return ``self._count`` corridors of differing length."""
        lat_span = destination[0] - origin[0]
        lon_span = destination[1] - origin[1]
        out: list[Route] = []
        for index in range(self._count):
            bend = index * 0.7
            geometry = [
                [
                    origin[1] + lon_span * i / 60 + bend * i / 60,
                    origin[0] + lat_span * i / 60 + bend * i / 40,
                ]
                for i in range(61)
            ]
            out.append(
                Route(
                    geometry=geometry,
                    distance_m=(300 + index * 20) * 1609.344,
                    duration_s=(5.2 + index * 0.4) * 3600,
                    source="osrm_alternative",
                )
            )
        return out


class ExplodingWeather:
    """A provider that always fails, for degradation tests."""

    name = "exploding"

    def fetch(self, locations, *, window):
        """Always fail."""
        from tripplan.adapters.errors import UpstreamError

        raise UpstreamError("open_meteo", "simulated outage", retryable=False)


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    """Drop cached analyses between tests.

    Without this the cache is shared process-wide, and because ``payload()``
    truncates the departure to whole seconds, two tests running inside the same
    second produce identical cache keys -- so an earlier test's cached result
    would silently satisfy a later one.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def registry() -> ServiceRegistry:
    """A registry wired to mocks, injected for the duration of the test."""
    return ServiceRegistry(
        geocoder=StubGeocoder(),
        router=StubRouter(),
        weather=MockWeatherProvider(),
    )


@pytest.fixture
def client(registry: ServiceRegistry, monkeypatch: pytest.MonkeyPatch) -> APIClient:
    """API client whose service is built from the mock registry."""
    monkeypatch.setattr(
        views,
        "_service",
        lambda: TripAnalysisService(registry),
    )
    return APIClient()


def payload(**overrides: Any) -> dict[str, Any]:
    """A valid request body."""
    base = {
        "origin": "Chicago, IL",
        "destination": "Detroit, MI",
        "departure": (datetime.now(UTC) + timedelta(hours=6)).replace(microsecond=0).isoformat(),
        "load_lb": 35_000,
        "spacing_mi": 25,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validation (BE-54)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"origin": ""}, "origin"),
        ({"destination": ""}, "destination"),
        ({"load_lb": -1}, "load_lb"),
        ({"load_lb": 200_000}, "load_lb"),
        ({"spacing_mi": 17}, "spacing_mi"),
        ({"spacing_mi": 0}, "spacing_mi"),
        ({"slider_hours": 49}, "slider_hours"),
        ({"slider_hours": -1}, "slider_hours"),
    ],
)
def test_invalid_fields_are_rejected_with_the_field_named(
    client: APIClient, overrides: dict, field: str
) -> None:
    response = client.post(reverse("tripplan:analyze"), payload(**overrides), format="json")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert field in body["error"]["details"]["fields"]


def test_missing_fields_are_reported_together(client: APIClient) -> None:
    """All problems at once, so the driver fixes the form in one pass."""
    response = client.post(reverse("tripplan:analyze"), {}, format="json")
    assert response.status_code == 400
    fields = response.json()["error"]["details"]["fields"]
    assert {"origin", "destination", "departure", "load_lb"} <= set(fields)


def test_departure_beyond_the_forecast_horizon_is_rejected(client: APIClient) -> None:
    far = (datetime.now(UTC) + timedelta(days=40)).replace(microsecond=0)
    response = client.post(
        reverse("tripplan:analyze"), payload(departure=far.isoformat()), format="json"
    )
    assert response.status_code == 400
    assert "departure" in response.json()["error"]["details"]["fields"]


def test_inverted_truck_speed_bounds_are_rejected(client: APIClient) -> None:
    response = client.post(
        reverse("tripplan:analyze"),
        payload(truck_min_avg_mph=70, truck_max_avg_mph=40),
        format="json",
    )
    assert response.status_code == 400
    assert "truck_min_avg_mph" in response.json()["error"]["details"]["fields"]


def test_naive_departure_is_accepted_with_a_warning(client: APIClient) -> None:
    naive = (datetime.now(UTC) + timedelta(hours=6)).replace(microsecond=0, tzinfo=None)
    response = client.post(
        reverse("tripplan:analyze"), payload(departure=naive.isoformat()), format="json"
    )
    assert response.status_code == 200
    assert any("UTC" in w for w in response.json()["warnings"])


# ---------------------------------------------------------------------------
# non-streaming analysis (BE-53)
# ---------------------------------------------------------------------------


def test_analyze_returns_the_full_payload(client: APIClient) -> None:
    response = client.post(reverse("tripplan:analyze"), payload(), format="json")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"meta", "routes", "summaries", "checkpoints", "recommendation", "warnings"}
    assert len(body["routes"]) == 3
    assert len(body["summaries"]) == 3
    assert len(body["checkpoints"]) == 3


def test_route_entries_carry_geometry_and_summary(client: APIClient) -> None:
    route = client.post(reverse("tripplan:analyze"), payload(), format="json").json()["routes"][0]
    assert route["label"].startswith("Option")
    assert len(route["geometry"]) > 2
    assert route["road_miles"] > 0
    assert route["truck_avg_mph"] >= 25
    assert route["checkpoint_count"] > 1
    assert "miles_by_level" in route["summary"]


def test_checkpoints_are_ordered_and_carry_hourly_risk(client: APIClient) -> None:
    body = client.post(reverse("tripplan:analyze"), payload(), format="json").json()
    points = body["checkpoints"][0]["points"]

    distances = [p["distance_mi"] for p in points]
    assert distances == sorted(distances)
    assert distances[0] == 0.0
    assert points[0]["is_origin"] and points[-1]["is_destination"]

    for point in points:
        assert point["eta"].endswith("+00:00")
        assert point["eta_hour_index"] >= 0
        assert len(point["hourly_risk"]) == body["meta"]["hour_window_hours"]
        assert 1 <= point["risk"]["level_value"] <= 5


def test_hourly_risk_alignment_supports_the_slider(client: APIClient) -> None:
    """The invariant the whole 0-48 h slider rests on.

    ``hourly_risk[eta_hour_index + offset]`` must be a level whose risk at that
    hour matches an independent re-evaluation. Contract-tested here so a change
    to indexing cannot silently break the frontend (D7/D12).
    """
    from tripplan.domain.risk.enums import RiskLevel

    body = client.post(reverse("tripplan:analyze"), payload(), format="json").json()
    for entry in body["checkpoints"]:
        for point in entry["points"]:
            index = point["eta_hour_index"]
            levels = point["hourly_risk"]
            # base level at offset 0 must equal the reported base risk
            assert RiskLevel.from_score(levels[index]).name == point["risk"]["level"]
            # and offsets must stay in range for the whole slider
            assert 0 <= index <= len(levels) - 1
            assert (
                index + body["meta"]["slider_hours"]
                <= len(levels) + body["meta"]["hour_window_hours"]
            )


def test_recommendation_names_a_route_that_exists(client: APIClient) -> None:
    body = client.post(reverse("tripplan:analyze"), payload(), format="json").json()
    rec = body["recommendation"]
    route_ids = {r["id"] for r in body["routes"]}

    assert rec["route_id"] in route_ids
    assert set(rec["ranking"]) <= route_ids
    assert len(rec["ranking"]) == len(body["routes"])
    assert rec["rationale"], "a recommendation must explain itself"
    assert rec["safety_gate_enabled"] is True


def test_meta_publishes_the_assumptions(client: APIClient) -> None:
    meta = client.post(reverse("tripplan:analyze"), payload(), format="json").json()["meta"]
    assert meta["time_snap"] == "floor"
    assert meta["spacing_mi"] == 25
    assert meta["truck"]["speed_factor"] == 0.85
    assert meta["hour_window_hours"] > 48, "the slider needs more than 48 hours of cover"


# ---------------------------------------------------------------------------
# streaming (BE-51 / BE-52)
# ---------------------------------------------------------------------------


def _stream(
    client: APIClient, body: dict[str, Any]
) -> tuple[int, list[dict[str, Any]], dict[str, str]]:
    response = client.post(reverse("tripplan:analyze-stream"), body, format="json")
    events = [json.loads(line) for line in response.streaming_content if line.strip()]
    return response.status_code, events, dict(response.items())


def test_stream_emits_events_in_contract_order(client: APIClient) -> None:
    status_code, events, headers = _stream(client, payload())

    assert status_code == 200
    assert headers["Content-Type"] == "application/x-ndjson"
    assert headers["X-Accel-Buffering"] == "no", "proxies must not buffer the stream"

    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert kinds.count("route") == 3
    assert kinds.count("summary") == 3
    assert kinds.count("checkpoints") == 3
    assert kinds.count("recommendation") == 1
    # geometry must precede the weather, which is the entire point of streaming
    assert kinds.index("route") < kinds.index("weather")
    assert kinds.index("summary") < kinds.index("recommendation")


def test_stream_ends_with_done_not_an_error(client: APIClient) -> None:
    _, events, _ = _stream(client, payload())
    assert events[-1]["type"] == "done"
    assert "warnings" in events[-1]
    assert events[-1]["weather_locations"] > 0


def test_stream_validation_error_is_a_real_status_code(client: APIClient) -> None:
    """Validation runs before the stream opens, so this is a JSON 400."""
    response = client.post(reverse("tripplan:analyze-stream"), payload(load_lb=-5), format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


def test_stream_reports_upstream_failure_as_an_error_event(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the stream is open the status is fixed, so failures ride as events."""
    from tripplan.adapters.errors import UpstreamError

    class Broken:
        name = "broken"

        def geocode(self, query: str) -> GeocodeResult:
            raise UpstreamError("open_meteo", "simulated outage", retryable=False)

        def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
            return []

    monkeypatch.setattr(
        views,
        "_service",
        lambda: TripAnalysisService(
            ServiceRegistry(geocoder=Broken(), router=StubRouter(), weather=MockWeatherProvider())
        ),
    )
    api = APIClient()
    _, events, _ = _stream(api, payload())

    assert events[-1]["type"] == "error"
    assert events[-1]["error"]["code"] == "upstream_unavailable"
    assert events[-1]["status"] == 502


def test_streaming_and_non_streaming_agree(client: APIClient) -> None:
    """BE-51/BE-53: one implementation, so the two views cannot drift."""
    body = payload()
    streamed: dict[str, Any] = {}
    _, events, _ = _stream(client, body)

    for event in events:
        if event["type"] == "route":
            streamed.setdefault("routes", []).append(
                {
                    "id": event["id"],
                    "label": event["label"],
                    "source": event["source"],
                    "road_miles": event["road_miles"],
                    "checkpoint_count": event["checkpoint_count"],
                }
            )
        elif event["type"] == "summary":
            streamed.setdefault("summaries", []).append(
                {
                    "route_id": event["route_id"],
                    "severe_miles": event["severe_miles"],
                    "high_miles": event["high_miles"],
                    "avg_risk": event["avg_risk"],
                }
            )
        elif event["type"] == "recommendation":
            streamed["recommendation"] = {
                "route_id": event["route_id"],
                "ranking": event["ranking"],
            }

    full = client.post(reverse("tripplan:analyze"), body, format="json").json()

    assert [r["id"] for r in streamed["routes"]] == [r["id"] for r in full["routes"]]
    assert [r["label"] for r in streamed["routes"]] == [r["label"] for r in full["routes"]]
    assert [s["severe_miles"] for s in streamed["summaries"]] == [
        s["severe_miles"] for s in full["summaries"]
    ]
    assert streamed["recommendation"]["ranking"] == full["recommendation"]["ranking"]


# ---------------------------------------------------------------------------
# degradation and error taxonomy (BE-55)
# ---------------------------------------------------------------------------


def test_weather_failure_degrades_to_a_warning_not_a_500(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing forecast should still produce a usable map."""
    monkeypatch.setattr(
        views,
        "_service",
        lambda: TripAnalysisService(
            ServiceRegistry(
                geocoder=StubGeocoder(), router=StubRouter(), weather=ExplodingWeather()
            )
        ),
    )
    response = APIClient().post(reverse("tripplan:analyze"), payload(), format="json")

    assert response.status_code == 200
    body = response.json()
    assert any("Weather data could not be retrieved" in w for w in body["warnings"])
    assert len(body["routes"]) == 3, "geometry is still useful without weather"
    assert body["recommendation"]["route_id"] in {r["id"] for r in body["routes"]}


def test_unknown_location_returns_404(client: APIClient) -> None:
    response = client.post(reverse("tripplan:analyze"), payload(origin="Atlantis"), format="json")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "location_not_found"


def test_fewer_than_three_routes_is_not_an_error(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        views,
        "_service",
        lambda: TripAnalysisService(
            ServiceRegistry(
                geocoder=StubGeocoder(), router=StubRouter(routes=2), weather=MockWeatherProvider()
            )
        ),
    )
    body = APIClient().post(reverse("tripplan:analyze"), payload(), format="json").json()
    assert len(body["routes"]) == 2
    assert len(body["recommendation"]["ranking"]) == 2


# ---------------------------------------------------------------------------
# caching (BE-58)
# ---------------------------------------------------------------------------


def test_second_identical_request_is_served_from_cache(client: APIClient, settings) -> None:
    settings.ANALYSIS_CACHE_TTL_SECONDS = 300
    body = payload()

    first = client.post(reverse("tripplan:analyze"), body, format="json").json()
    assert first["meta"]["cached"] is False

    second = client.post(reverse("tripplan:analyze"), body, format="json").json()
    assert second["meta"]["cached"] is True
    assert second["recommendation"]["route_id"] == first["recommendation"]["route_id"]
    assert [r["id"] for r in second["routes"]] == [r["id"] for r in first["routes"]]


def test_a_different_load_is_not_served_from_the_same_cache_entry(
    client: APIClient, settings
) -> None:
    settings.ANALYSIS_CACHE_TTL_SECONDS = 300
    client.post(reverse("tripplan:analyze"), payload(load_lb=10_000), format="json")
    other = client.post(reverse("tripplan:analyze"), payload(load_lb=45_000), format="json").json()
    assert other["meta"]["cached"] is False


def test_cached_stream_replays_full_meta(client: APIClient, settings) -> None:
    """The cached path must still emit hour_window_start, or the slider breaks."""
    settings.ANALYSIS_CACHE_TTL_SECONDS = 300
    body = payload()
    client.post(reverse("tripplan:analyze"), body, format="json")

    _, events, _ = _stream(client, body)
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["cached"] is True
    assert "hour_window_start" in meta
    assert meta["hour_window_hours"] > 48

    points = next(e for e in events if e["type"] == "checkpoints")["points"]
    assert len(points[0]["hourly_risk"]) == meta["hour_window_hours"]
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# config (BE-56 / D12)
# ---------------------------------------------------------------------------


def test_config_publishes_every_rule(client: APIClient) -> None:
    body = client.get(reverse("tripplan:config")).json()

    assert len(body["levels"]) == 5
    assert {level["name"] for level in body["levels"]} == {
        "LOW",
        "MODERATE",
        "HIGH",
        "SEVERE",
        "NO_TRAVEL",
    }
    assert all(level["hex"].startswith("#") for level in body["levels"])

    variables = {t["variable"] for t in body["thresholds"]}
    assert variables == {"wind", "rain", "snow"}

    policy = body["load_policy"]
    assert policy["heavy_load_threshold_lb"] == 30_000
    assert policy["extra_heavy_threshold_lb"] == 40_000
    assert policy["boundary_inclusive"] is False
    assert policy["max_load_lb"] == 80_000


def test_config_states_the_assumptions_the_ui_must_disclose(client: APIClient) -> None:
    """D12/D15: the methodology panel renders from this, so the caveats live here."""
    body = client.get(reverse("tripplan:config")).json()
    assumptions = body["assumptions"]

    assert assumptions["checkpoint_time_snap"] == "floor"
    assert assumptions["weather_resolution"] == "hourly"
    assert "DEPTH" in assumptions["note"]
    assert body["slider"]["semantics"] == "departure_offset"

    mapping = {m["variable"]: m["fields"] for m in body["variable_mapping"]}
    assert mapping["rain"] == ["rain", "showers"]
    assert mapping["snow"] == ["snowfall"]
    assert "precipitation" not in mapping["rain"]


def test_config_threshold_ranges_match_the_published_table(client: APIClient) -> None:
    body = client.get(reverse("tripplan:config")).json()
    by_variable = {t["variable"]: t for t in body["thresholds"]}

    wind = [b["range"] for b in by_variable["wind"]["buckets"]]
    assert wind[0] == "<25"
    assert wind[-1] == ">=55", "wind no-travel is inclusive"

    rain = [b["range"] for b in by_variable["rain"]["buckets"]]
    assert rain[-1] == ">1", "rain no-travel is exclusive"

    snow = [b["range"] for b in by_variable["snow"]["buckets"]]
    assert snow[-1] == ">3"


def test_health_needs_no_upstream_call(client: APIClient) -> None:
    body = client.get(reverse("tripplan:health")).json()
    assert body["status"] == "ok"
    assert "weather_provider" in body
