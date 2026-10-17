"""Load-rule escalation tests (BE-24).

Two properties are asserted exhaustively here, because both are easy to get
subtly wrong and neither is exercised by a single hand-picked case:

* **Escalation only.** No load may lower a level (D2).
* **Strict ``>`` on the load bounds.** Exactly 30,000 lb and exactly 40,000 lb do
  not escalate; one pound more does (D16).
"""

from __future__ import annotations

import itertools

import pytest

from tripplan.domain.risk.classify import classify_wind
from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.load_rules import (
    DEFAULT_LOAD_POLICY,
    LoadPolicy,
    apply_load_rules,
)

LOADS = [0, 25_000, 30_000, 30_001, 40_000, 40_001, 45_000, 80_000]
WINDS = [0.0, 30.0, 35.0, 40.0, 44.9, 45.0, 50.0, 54.9, 55.0, 70.0]


def test_heavy_load_in_severe_band_becomes_no_travel() -> None:
    """45-54 mph with more than 30,000 lb is No Travel."""
    result = apply_load_rules(48.0, RiskLevel.SEVERE, 35_000)
    assert result.level is RiskLevel.NO_TRAVEL
    assert result.escalated
    assert result.rule == "severe_wind_heavy_load"
    assert "30,000" in (result.reason or "")


def test_light_load_in_severe_band_is_untouched() -> None:
    """The same wind with a light load stays Severe."""
    result = apply_load_rules(48.0, RiskLevel.SEVERE, 25_000)
    assert result.level is RiskLevel.SEVERE
    assert not result.escalated


def test_extra_heavy_load_in_high_band_becomes_severe() -> None:
    """35-44 mph with more than 40,000 lb escalates to at least Severe."""
    result = apply_load_rules(40.0, RiskLevel.HIGH, 45_000)
    assert result.level is RiskLevel.SEVERE
    assert result.rule == "high_wind_extra_heavy_load"


def test_fifty_five_mph_is_no_travel_for_any_load() -> None:
    """>= 55 mph blocks the corridor regardless of weight, even empty."""
    for load in LOADS:
        result = apply_load_rules(55.0, RiskLevel.NO_TRAVEL, load)
        assert result.level is RiskLevel.NO_TRAVEL
        assert result.rule == "wind_no_travel"


@pytest.mark.parametrize(
    ("wind", "load", "should_escalate"),
    [
        # Strictness at the 30,000 lb boundary, inside the Severe band.
        (48.0, 30_000, False),
        (48.0, 30_001, True),
        # Strictness at the 40,000 lb boundary, inside the High band.
        (40.0, 40_000, False),
        (40.0, 40_001, True),
        # A load below the threshold never escalates, whatever the band.
        (40.0, 39_999, False),
        (48.0, 29_999, False),
    ],
)
def test_load_boundaries_are_strict(wind: float, load: int, *, should_escalate: bool) -> None:
    """The table writes ">30,000" and ">40,000", so equality does not trigger."""
    result = apply_load_rules(wind, classify_wind(wind).level, load)
    assert result.escalated is should_escalate


def test_inclusive_boundary_policy_flips_exactly_at_the_threshold() -> None:
    """The D16 escape hatch changes one boolean and nothing else."""
    inclusive = LoadPolicy(boundary_inclusive=True)
    assert inclusive.load_triggers(30_000, 30_000) is True
    assert inclusive.load_triggers(29_999, 30_000) is False
    assert DEFAULT_LOAD_POLICY.load_triggers(30_000, 30_000) is False


@pytest.mark.parametrize(("wind", "load"), list(itertools.product(WINDS, LOADS)))
def test_load_rules_never_lower_a_level(wind: float, load: int) -> None:
    """Escalation is monotonic: the result is always at least as severe (D2)."""
    base = classify_wind(wind).level
    result = apply_load_rules(wind, base, load)
    assert result.level >= base


@pytest.mark.parametrize(("wind", "load"), list(itertools.product(WINDS, LOADS)))
def test_escalation_matches_the_published_rules(wind: float, load: int) -> None:
    """A direct restatement of the table, independent of the implementation."""
    base = classify_wind(wind).level
    result = apply_load_rules(wind, base, load)

    if wind >= 55 or (wind >= 45 and load > 30_000):
        expected = RiskLevel.NO_TRAVEL
    elif wind >= 35 and load > 40_000:
        expected = RiskLevel.worst([base, RiskLevel.SEVERE])
    else:
        expected = base

    assert result.level is expected


def test_missing_wind_skips_load_rules() -> None:
    """No reading means no escalation, rather than a guessed speed."""
    result = apply_load_rules(None, RiskLevel.LOW, 80_000)
    assert result.level is RiskLevel.LOW
    assert not result.escalated
