"""Mileage accounting tests (BE-26).

The headline assertions are hand-computed rather than snapshotted, so a change in
behaviour has to be justified against arithmetic a reviewer can check.

The worked example used throughout: a 100-mile route sampled every 25 miles, so
checkpoints sit at 0, 25, 50, 75 and 100. Midpoint attribution splits the road
into five bands::

    0        25        50        75       100
    |---------^---------|---------|---------|
         12.5      37.5     62.5     87.5    <- band edges
     [  12.5 ][  25  ][  25  ][  25  ][ 12.5 ]
       cp0      cp1     cp2     cp3     cp4
"""

from __future__ import annotations

import random

import pytest

from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.miles import attribute_segments, summarise

L = RiskLevel


def test_midpoint_bands_cover_the_route_exactly() -> None:
    distances = [0.0, 25.0, 50.0, 75.0, 100.0]
    segments = attribute_segments(distances)

    assert [s.miles for s in segments] == [12.5, 25.0, 25.0, 25.0, 12.5]
    assert segments[0].start_mi == 0.0
    assert segments[-1].end_mi == 100.0
    assert sum(s.miles for s in segments) == pytest.approx(100.0)


def test_hand_computed_mileage_totals() -> None:
    """LOW, LOW, SEVERE, SEVERE, HIGH over the 100-mile example."""
    summary = summarise(
        [0.0, 25.0, 50.0, 75.0, 100.0],
        [L.LOW, L.LOW, L.SEVERE, L.SEVERE, L.HIGH],
    )

    # cp0 -> 12.5 LOW, cp1 -> 25 LOW, cp2 -> 25 SEVERE, cp3 -> 25 SEVERE, cp4 -> 12.5 HIGH
    assert summary.severe_miles == pytest.approx(50.0)
    assert summary.high_miles == pytest.approx(12.5)
    assert summary.low_miles == pytest.approx(37.5)
    assert summary.moderate_miles == 0.0
    assert summary.no_travel_miles == 0.0
    assert summary.adverse_miles == pytest.approx(62.5)
    assert summary.total_miles == pytest.approx(100.0)
    assert summary.worst_level is L.SEVERE


def test_hand_computed_average_risk() -> None:
    """Avg = sum(miles x score) / total = (12.5 + 25 + 100 + 100 + 37.5) / 100."""
    summary = summarise(
        [0.0, 25.0, 50.0, 75.0, 100.0],
        [L.LOW, L.LOW, L.SEVERE, L.SEVERE, L.HIGH],
    )
    assert summary.avg_risk == pytest.approx(2.75)


def test_uniform_risk_gives_that_score() -> None:
    """A uniformly High route averages exactly 3.0, whatever the spacing."""
    for spacing in (10, 25, 50):
        distances = [float(i * spacing) for i in range(5)]
        summary = summarise(distances, [L.HIGH] * 5)
        assert summary.avg_risk == pytest.approx(3.0)


def test_miles_by_level_sums_to_total_for_random_routes() -> None:
    """Property test: the bands partition the route for any sampling and levels."""
    rng = random.Random(20261017)
    for _ in range(400):
        count = rng.randint(1, 60)
        step = rng.choice([1.0, 5.0, 10.0, 25.0, 50.0])
        distances = [i * step for i in range(count)]
        levels = [rng.choice(list(RiskLevel)) for _ in range(count)]

        summary = summarise(distances, levels)
        assert sum(summary.miles_by_level.values()) == pytest.approx(summary.total_miles)
        assert summary.total_miles == pytest.approx(distances[-1])


def test_no_travel_miles_are_reported_separately_from_severe() -> None:
    """The safety gate needs its own figure, not folded into Severe."""
    summary = summarise([0.0, 10.0], [L.NO_TRAVEL, L.SEVERE])
    assert summary.no_travel_miles == pytest.approx(5.0)
    assert summary.severe_miles == pytest.approx(5.0)
    assert summary.blocked_miles == summary.no_travel_miles


def test_single_checkpoint_route() -> None:
    """A zero-length route still produces a coherent summary."""
    summary = summarise([0.0], [L.LOW])
    assert summary.total_miles == 0.0
    assert summary.avg_risk == 0.0
    assert summary.miles_by_level == {L.LOW: 0.0}


def test_empty_input_is_handled() -> None:
    summary = summarise([], [])
    assert summary.total_miles == 0.0
    assert summary.worst_level is L.LOW
    assert summary.segments == ()


@pytest.mark.parametrize(
    "distances",
    [
        [10.0, 10.0],  # not strictly increasing
        [0.0, 20.0, 15.0],  # goes backwards
    ],
)
def test_non_increasing_distances_are_rejected(distances: list[float]) -> None:
    """Bad sampling data must fail loudly rather than produce negative miles."""
    with pytest.raises(ValueError, match="strictly increasing"):
        attribute_segments(distances)


def test_mismatched_lengths_are_rejected() -> None:
    with pytest.raises(ValueError, match="distances but"):
        summarise([0.0, 10.0], [L.LOW])


def test_empty_distances_are_rejected() -> None:
    with pytest.raises(ValueError, match="no checkpoints"):
        attribute_segments([])


def test_summary_serialisation_is_rounded_for_display() -> None:
    payload = summarise([0.0, 25.0, 50.0], [L.LOW, L.HIGH, L.NO_TRAVEL]).to_dict()
    assert payload["miles_by_level"]["NO_TRAVEL"] == 12.5
    assert payload["worst_level"] == "NO_TRAVEL"
    assert isinstance(payload["avg_risk"], float)
