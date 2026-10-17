"""Geometry, sampling and ETA tests (BE-17, BE-18, BE-19, BE-28)."""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest

from tripplan.domain.geo.eta import (
    TruckProfile,
    cumulative_eta,
    eta_plan,
    hour_index,
    rest_hours,
)
from tripplan.domain.geo.haversine import (
    cumulative_distances,
    haversine_miles,
    line_length_miles,
    point_at_distance,
    simplify_tolerance_mi,
)
from tripplan.domain.geo.sampling import sample_checkpoints, sample_distances, sampling_plan

# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------


def test_reference_great_circle_distances() -> None:
    """Measured values; Denver->Chicago is 918 mi straight-line, 1,006 mi driven."""
    assert haversine_miles(39.7392, -104.9903, 41.8781, -87.6298) == pytest.approx(918.2, abs=0.5)
    assert haversine_miles(41.8781, -87.6298, 43.0389, -87.9065) == pytest.approx(81.43, abs=0.05)


def test_identical_points_are_zero_distance() -> None:
    assert haversine_miles(41.0, -87.0, 41.0, -87.0) == 0.0


def test_distance_is_symmetric() -> None:
    a = haversine_miles(41.0, -87.0, 39.0, -105.0)
    b = haversine_miles(39.0, -105.0, 41.0, -87.0)
    assert a == pytest.approx(b)


def test_cumulative_distances_start_at_zero_and_increase() -> None:
    coords = [(-87.63, 41.88), (-87.70, 41.90), (-87.80, 41.95)]
    distances = cumulative_distances(coords)
    assert distances[0] == 0.0
    assert all(b > a for a, b in itertools.pairwise(distances))
    assert distances[-1] == pytest.approx(line_length_miles(coords))


def test_empty_polyline_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty polyline"):
        cumulative_distances([])


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "spacing", "expected"),
    [
        (100.0, 25.0, [0, 25, 50, 75, 100]),
        (100.0, 10.0, [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]),
        (100.0, 50.0, [0, 50, 100]),
        (100.0, 30.0, [0, 30, 60, 90, 100]),  # destination appended
        (5.0, 25.0, [0, 5]),  # shorter than one interval
        (25.0, 25.0, [0, 25]),  # exactly one interval
        (24.0, 25.0, [0, 24]),
    ],
)
def test_sample_distances(total: float, spacing: float, expected: list[float]) -> None:
    """Interior points land on exact multiples; the destination is always included."""
    assert sample_distances(total, spacing) == pytest.approx([float(v) for v in expected])


def test_sample_distances_rejects_non_positive_spacing() -> None:
    with pytest.raises(ValueError, match="spacing_mi must be positive"):
        sample_distances(100.0, 0.0)


def test_checkpoints_are_ordered_and_endpoints_flagged() -> None:
    coords = [(-87.6298, 41.8781), (-87.80, 41.95), (-87.95, 42.10)]
    checkpoints = sample_checkpoints(coords, 25.0)

    assert len(checkpoints) >= 2
    assert checkpoints[0].is_origin
    assert checkpoints[-1].is_destination
    assert all(b.distance_mi > a.distance_mi for a, b in itertools.pairwise(checkpoints))


@pytest.mark.parametrize("spacing", [10, 25, 50])
def test_spacing_is_honoured_in_the_interior(spacing: int) -> None:
    """Interior checkpoints are exactly `spacing` miles apart (D-invariant of D4)."""
    coords = [(-88.5 + 0.05 * i, 41.88 + 0.05 * i) for i in range(40)]
    checkpoints = sample_checkpoints(coords, float(spacing))

    interior = [c for c in checkpoints if not c.is_destination]
    gaps = [b.distance_mi - a.distance_mi for a, b in itertools.pairwise(interior)]
    assert gaps, "expected interior checkpoints"
    assert all(gap == pytest.approx(spacing, abs=0.05) for gap in gaps)


def test_points_advance_along_the_polyline() -> None:
    coords = [(-88.0 + i * 0.01, 42.0) for i in range(50)]
    checkpoints = sample_checkpoints(coords, 10.0)
    lons = [c.lon for c in checkpoints]
    assert lons == sorted(lons), "checkpoints must progress west to east"


def test_point_at_distance_interpolates() -> None:
    coords = [(-87.0, 42.0), (-87.0, 42.10)]
    distances = cumulative_distances(coords)
    total = distances[-1]

    lat, lon = point_at_distance(coords, distances, total / 2)
    assert lat == pytest.approx(42.05, abs=0.005)
    assert lon == pytest.approx(-87.0, abs=1e-6)

    # Out-of-range targets clamp rather than raise.
    assert point_at_distance(coords, distances, -5.0) == (42.0, -87.0)
    assert point_at_distance(coords, distances, total + 100) == (42.10, -87.0)


def test_sampling_plan_reports_counts() -> None:
    plan = sampling_plan(100.0, 25.0)
    assert plan == {"spacing_mi": 25.0, "route_miles": 100.0, "checkpoint_count": 5}


def test_simplification_tolerance_grows_with_route_length() -> None:
    tolerances = [simplify_tolerance_mi(d) for d in (10, 100, 400, 1000, 2000)]
    assert tolerances == sorted(tolerances)
    assert tolerances[0] < tolerances[-1]


# ---------------------------------------------------------------------------
# ETA
# ---------------------------------------------------------------------------


def test_provider_speed_is_adjusted_downward_for_a_truck() -> None:
    """A 56.4 mph car route is not a truck's sustained average (D6)."""
    profile = TruckProfile()
    # 1,006 miles in 17.82 h => 56.4 mph implied by the car profile.
    assert profile.truck_avg_mph(1006.0, 17.82 * 3600) == pytest.approx(56.4 * 0.85, rel=0.01)


def test_speed_is_clamped_to_the_profile_bounds() -> None:
    profile = TruckProfile()
    assert profile.truck_avg_mph(1_000.0, 1.0) == profile.max_avg_mph  # absurdly fast
    assert profile.truck_avg_mph(100.0, 100_000.0) == profile.min_avg_mph  # absurdly slow


def test_unknown_duration_falls_back_instead_of_dividing_by_zero() -> None:
    profile = TruckProfile()
    midpoint = (profile.min_avg_mph + profile.max_avg_mph) / 2
    assert profile.truck_avg_mph(500.0, 0.0) == pytest.approx(midpoint)


def test_etas_are_monotonic_and_start_at_departure() -> None:
    departure = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    distances = [0.0, 25.0, 50.0, 75.0, 100.0]

    arrivals = cumulative_eta(departure, distances, avg_mph=50.0)
    assert arrivals[0] == departure
    assert all(b > a for a, b in itertools.pairwise(arrivals))


def test_total_eta_matches_the_driving_time() -> None:
    departure = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    distances = [0.0, 50.0, 100.0]

    arrivals = cumulative_eta(departure, distances, avg_mph=50.0)
    assert arrivals[-1] - departure == timedelta(hours=2.0)


def test_naive_departure_is_treated_as_utc() -> None:
    naive = datetime(2026, 10, 17, 6, 0)
    aware = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    assert cumulative_eta(naive, [0.0], 50.0) == cumulative_eta(aware, [0.0], 50.0)


def test_eta_plan_resolves_speed_and_total_time() -> None:
    avg_mph, hours = eta_plan(
        datetime.now(UTC),
        provider_distance_mi=1000.0,
        provider_duration_s=17.0 * 3600,
    )
    assert 25.0 <= avg_mph <= 65.0
    assert hours == pytest.approx(1000.0 / avg_mph)


def test_rest_breaks_extend_the_schedule() -> None:
    profile = TruckProfile(break_after_hours=11.0, break_duration_minutes=30.0)
    assert rest_hours(10.0, profile) == 0.0
    assert rest_hours(11.0, profile) == pytest.approx(0.5)
    assert rest_hours(22.0, profile) == pytest.approx(1.0)

    departure = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    distances = [float(i * 300) for i in range(6)]  # 1,500 miles at 50 mph = 30 h
    arrivals = cumulative_eta(departure, distances, avg_mph=50.0, profile=profile)
    assert arrivals[-1] - departure == timedelta(hours=30 + 1.0)


def test_breaks_are_disabled_by_default() -> None:
    profile = TruckProfile()
    assert profile.break_after_hours == 0.0
    assert rest_hours(100.0, profile) == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"speed_factor": 0.0},
        {"speed_factor": -1.0},
        {"min_avg_mph": 70.0, "max_avg_mph": 55.0},
        {"min_avg_mph": 0.0},
    ],
)
def test_invalid_profiles_are_rejected(kwargs: dict[str, float]) -> None:
    """A misconfigured profile must fail at construction, not silently mis-estimate."""
    with pytest.raises(ValueError):
        TruckProfile(**kwargs)


# ---------------------------------------------------------------------------
# Checkpoint hour snapping (D14)
# ---------------------------------------------------------------------------


def test_floor_snaps_to_the_hour_at_or_before_arrival() -> None:
    window = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    assert hour_index(datetime(2026, 10, 17, 6, 0, tzinfo=UTC), window) == 6
    assert hour_index(datetime(2026, 10, 17, 6, 37, tzinfo=UTC), window) == 6
    assert hour_index(datetime(2026, 10, 17, 6, 59, tzinfo=UTC), window) == 6


def test_snap_modes_differ_by_at_most_one_index() -> None:
    """The documented bound on the time-snap (D14)."""
    window = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    rng = random.Random(99)
    for _ in range(500):
        minutes = rng.randint(0, 59)
        eta = window + timedelta(hours=rng.randint(0, 72), minutes=minutes)
        indices = {hour_index(eta, window, snap) for snap in ("floor", "ceil", "nearest")}
        assert max(indices) - min(indices) <= 1


def test_hour_index_never_goes_negative() -> None:
    window = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert hour_index(datetime(2026, 10, 17, 6, 0, tzinfo=UTC), window) == 0


def test_unknown_snap_mode_is_rejected() -> None:
    window = datetime(2026, 10, 17, tzinfo=UTC)
    with pytest.raises(ValueError, match="Unknown snap mode"):
        hour_index(window, window, "round")


def test_hour_index_handles_naive_datetimes() -> None:
    naive = datetime(2026, 10, 17, 0, 0)
    assert hour_index(datetime(2026, 10, 17, 3, 30), naive) == 3


def test_truck_speed_factor_slows_etas_proportionally() -> None:
    """A heavier assumption must produce strictly later arrivals."""
    departure = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    distances = [0.0, 100.0]
    # 100 miles in 2 h implies 50 mph from the provider: unclamped at both factors.
    provider_distance_mi, provider_duration_s = 100.0, 2 * 3600.0

    brisk = TruckProfile(speed_factor=1.0, min_avg_mph=10.0, max_avg_mph=90.0)
    cautious = TruckProfile(speed_factor=0.5, min_avg_mph=10.0, max_avg_mph=90.0)

    assert brisk.truck_avg_mph(provider_distance_mi, provider_duration_s) == pytest.approx(50.0)
    assert cautious.truck_avg_mph(provider_distance_mi, provider_duration_s) == pytest.approx(25.0)

    assert (
        cumulative_eta(departure, distances, 50.0, brisk)[1]
        < cumulative_eta(departure, distances, 25.0, cautious)[1]
    )
