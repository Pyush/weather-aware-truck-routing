"""Checkpoint aggregation tests (BE-25).

The overall level is the worst of the three variables (D3). These tests pin the
aggregation itself *and* the reasoning strings, because the UI renders those
strings in map tooltips -- an explanation that disagrees with the colour is worse
than no explanation.
"""

from __future__ import annotations

import pytest

from tripplan.domain.risk.classify import MISSING_VALUE_LEVEL
from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.evaluate import WeatherReading, evaluate_checkpoint


def reading(wind=None, rain=None, snow=None) -> dict[str, float | None]:
    """Build a provider-shaped reading from friendly names."""
    return {
        "wind_speed_10m": wind,
        "rain": rain,
        "showers": None,
        "snowfall": snow,
        "precipitation": (rain or 0.0) + (snow or 0.0),
    }


def test_worst_variable_sets_the_level() -> None:
    """Severe wind with calm rain and snow is Severe, driven by wind."""
    result = evaluate_checkpoint(reading(wind=50.0, rain=0.0, snow=0.0), load_lb=0)
    assert result.level is RiskLevel.SEVERE
    assert result.dominant_factor == "wind"


def test_snow_can_set_the_level_on_its_own() -> None:
    """Calm wind and no rain, but 1.4 in/hr of snow is HIGH."""
    result = evaluate_checkpoint(reading(wind=5.0, rain=0.0, snow=1.4), load_lb=0)
    assert result.level is RiskLevel.HIGH
    assert result.dominant_factor == "snow"


def test_no_travel_dominates_a_high_rain_reading() -> None:
    """The worst reading wins outright."""
    result = evaluate_checkpoint(reading(wind=5.0, rain=0.4, snow=0.0), load_lb=0)
    assert result.level is RiskLevel.HIGH
    assert result.dominant_factor == "rain"

    blocked = evaluate_checkpoint(reading(wind=60.0, rain=0.4, snow=0.0), load_lb=0)
    assert blocked.level is RiskLevel.NO_TRAVEL
    assert blocked.dominant_factor == "wind"


def test_all_clear_is_low() -> None:
    result = evaluate_checkpoint(reading(wind=10.0, rain=0.0, snow=0.0), load_lb=45_000)
    assert result.level is RiskLevel.LOW
    assert result.reasons  # an all-clear still explains itself


def test_dominant_factor_tie_breaks_to_wind() -> None:
    """When two variables share the top level, wind is named first."""
    result = evaluate_checkpoint(reading(wind=40.0, rain=0.3, snow=0.0), load_lb=0)
    assert result.level is RiskLevel.HIGH
    assert result.dominant_factor == "wind"


def test_load_escalation_drives_the_level_and_is_explained() -> None:
    """The reasoning string must name the load rule that fired."""
    result = evaluate_checkpoint(reading(wind=48.0, rain=0.0, snow=0.0), load_lb=35_000)
    assert result.level is RiskLevel.NO_TRAVEL
    assert result.dominant_factor == "wind"
    assert any("35,000 lb" in reason for reason in result.reasons)


def test_reasons_are_never_duplicated() -> None:
    """A reason is emitted once even when several variables agree."""
    result = evaluate_checkpoint(reading(wind=36.0, rain=0.0, snow=0.0), load_lb=45_000)
    assert len(result.reasons) == len(set(result.reasons))


@pytest.mark.parametrize(
    ("wind", "rain", "snow", "expected_quality"),
    [
        (10.0, 0.0, 0.0, "ok"),
        (10.0, None, 0.0, "partial"),
        (None, None, None, "missing"),
    ],
)
def test_data_quality_is_reported(
    wind: float | None,
    rain: float | None,
    snow: float | None,
    *,
    expected_quality: str,
) -> None:
    """Missing readings are surfaced, never silently treated as calm."""
    result = evaluate_checkpoint(reading(wind=wind, rain=rain, snow=snow), load_lb=0)
    assert result.data_quality == expected_quality


def test_missing_reading_does_not_inflate_risk() -> None:
    """Absent data must not read as hazardous (D3/BE-12)."""
    result = evaluate_checkpoint(reading(wind=None, rain=None, snow=None), load_lb=45_000)
    assert result.level is MISSING_VALUE_LEVEL
    assert result.level is RiskLevel.LOW


def test_d15_snow_is_read_as_depth_not_liquid_equivalent() -> None:
    """1.4 in/hr of snow is HIGH; its ~0.2 in/hr liquid equivalent would only be Moderate.

    Regression guard for the unit trap documented in D15: if the Snow rule were
    ever fed ``precipitation`` instead of ``snowfall``, a serious snow band would
    silently under-report by two levels.
    """
    from tripplan.domain.risk.classify import classify_rain, classify_snow

    assert classify_snow(1.4).level is RiskLevel.HIGH
    assert classify_rain(0.2).level is RiskLevel.MODERATE  # the liquid equivalent

    correct = evaluate_checkpoint(reading(wind=5.0, rain=0.0, snow=1.4), load_lb=0)
    assert correct.level is RiskLevel.HIGH


def test_rain_rule_uses_liquid_precipitation_only() -> None:
    """Rain and showers sum; snowfall must not leak into the Rain reading."""
    from tripplan.domain.risk.weather_vars import MAPPINGS

    rain_mapping = MAPPINGS["rain"]
    both = {"rain": 0.10, "showers": 0.05, "snowfall": 2.0}
    assert rain_mapping.extract(both) == pytest.approx(0.15)
    assert rain_mapping.extract({"rain": 0.0, "showers": 0.0, "snowfall": 2.0}) == 0.0
    assert rain_mapping.extract({"snowfall": 2.0}) is None


def test_weather_reading_normalises_provider_fields() -> None:
    """Units are converted once, at the edge, rather than inside the rules."""
    normalised = WeatherReading.from_reading(
        {
            "wind_speed_10m": 30.0,
            "rain": 0.1,
            "showers": 0.05,
            "snowfall": 0.3,
            "precipitation": 0.45,
        }
    )
    assert normalised.wind_mph == 30.0
    assert normalised.rain_in_hr == pytest.approx(0.15)
    assert normalised.snow_in_hr == 0.3
    assert normalised.extra == {"precipitation": 0.45}

    # wind 30 mph -> Moderate, rain 0.15 in/hr -> Moderate, snow 0.3 in/hr -> Low.
    assert normalised.evaluate(load_lb=0).level is RiskLevel.MODERATE


def test_serialisation_exposes_colors_and_values() -> None:
    """The payload carries everything the tooltip needs."""
    payload = evaluate_checkpoint(reading(wind=48.0, rain=0.0, snow=0.0), load_lb=35_000).to_dict()
    assert payload["level"] == "NO_TRAVEL"
    assert payload["level_value"] == 5
    assert payload["dominant_factor"] == "wind"
    assert payload["wind"]["value"] == 48.0
    assert payload["wind"]["unit"] == "mph"
    assert isinstance(payload["reasons"], list)
