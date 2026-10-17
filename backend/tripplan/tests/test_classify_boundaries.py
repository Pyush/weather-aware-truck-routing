"""Golden rule-table tests (BE-29) plus the per-variable boundary matrices.

The three published bands differ in how their endpoints behave, so each variable
gets its own explicit matrix rather than one shared "boundary" helper:

===========  ==================  ==============  =========
Variable     No Travel is       Severe top is   Mid-bands
===========  ==================  ==============  =========
wind         inclusive (``>=``)  exclusive       half-open
rain         exclusive (``>``)   inclusive       half-open
snow         exclusive (``>``)   inclusive       half-open
===========  ==================  ==============  =========

A shared helper would have had to encode all three variants, which is exactly the
kind of conditional that eventually hides one of the cases (D1).
"""

from __future__ import annotations

import csv
import itertools

import pytest

from tripplan.domain.risk.classify import classify, classify_rain, classify_snow, classify_wind
from tripplan.domain.risk.enums import RiskLevel
from tripplan.domain.risk.thresholds import BUCKETS_BY_VARIABLE, find_bucket

# ---------------------------------------------------------------------------
# BE-21: wind boundary matrix
# ---------------------------------------------------------------------------

WIND_MATRIX = [
    (0.0, RiskLevel.LOW),
    (24.9, RiskLevel.LOW),
    (25.0, RiskLevel.MODERATE),
    (34.9, RiskLevel.MODERATE),
    (35.0, RiskLevel.HIGH),
    (44.9, RiskLevel.HIGH),
    (45.0, RiskLevel.SEVERE),
    (54.9, RiskLevel.SEVERE),
    (55.0, RiskLevel.NO_TRAVEL),
    (70.0, RiskLevel.NO_TRAVEL),
]


@pytest.mark.parametrize(("mph", "expected"), WIND_MATRIX, ids=lambda v: str(v))
def test_wind_boundaries(mph: float, expected: RiskLevel) -> None:
    """Wind: No Travel begins *at* 55, not after it."""
    assert classify_wind(mph).level is expected


def test_wind_no_travel_bound_is_inclusive() -> None:
    """The published table writes ">= 55 mph"."""
    assert classify_wind(55.0).level is RiskLevel.NO_TRAVEL
    assert classify_wind(54.999).level is RiskLevel.SEVERE


def test_wind_mid_bands_are_half_open() -> None:
    """Labels read 25-34 / 35-44 / 45-54, but the API returns decimals."""
    assert classify_wind(34.999).level is RiskLevel.MODERATE
    assert classify_wind(35.0).level is RiskLevel.HIGH


# ---------------------------------------------------------------------------
# BE-22: rain boundary matrix
# ---------------------------------------------------------------------------

RAIN_MATRIX = [
    (0.0, RiskLevel.LOW),
    (0.099, RiskLevel.LOW),
    (0.10, RiskLevel.MODERATE),
    (0.249, RiskLevel.MODERATE),
    (0.25, RiskLevel.HIGH),
    (0.499, RiskLevel.HIGH),
    (0.50, RiskLevel.SEVERE),
    (1.00, RiskLevel.SEVERE),
    (1.01, RiskLevel.NO_TRAVEL),
    (2.50, RiskLevel.NO_TRAVEL),
]


@pytest.mark.parametrize(("in_hr", "expected"), RAIN_MATRIX, ids=lambda v: str(v))
def test_rain_boundaries(in_hr: float, expected: RiskLevel) -> None:
    """Rain: No Travel is *strictly* greater than 1.00, and 1.00 is Severe."""
    assert classify_rain(in_hr).level is expected


def test_rain_severe_band_is_closed_at_the_top() -> None:
    """The table writes "0.50-1.00", so exactly 1.00 in/hr is Severe."""
    assert classify_rain(1.0).level is RiskLevel.SEVERE
    assert classify_rain(1.000001).level is RiskLevel.NO_TRAVEL


# ---------------------------------------------------------------------------
# BE-23: snow boundary matrix
# ---------------------------------------------------------------------------

SNOW_MATRIX = [
    (0.0, RiskLevel.LOW),
    (0.49, RiskLevel.LOW),
    (0.50, RiskLevel.MODERATE),
    (0.99, RiskLevel.MODERATE),
    (1.00, RiskLevel.HIGH),
    (1.99, RiskLevel.HIGH),
    (2.00, RiskLevel.SEVERE),
    (3.00, RiskLevel.SEVERE),
    (3.01, RiskLevel.NO_TRAVEL),
    (5.00, RiskLevel.NO_TRAVEL),
]


@pytest.mark.parametrize(("in_hr", "expected"), SNOW_MATRIX, ids=lambda v: str(v))
def test_snow_boundaries(in_hr: float, expected: RiskLevel) -> None:
    """Snow: No Travel is *strictly* greater than 3.0, and 3.0 is Severe."""
    assert classify_snow(in_hr).level is expected


def test_snow_severe_band_is_closed_at_the_top() -> None:
    """The table writes "2.0-3.0", so exactly 3.0 in/hr is Severe."""
    assert classify_snow(3.0).level is RiskLevel.SEVERE
    assert classify_snow(3.000001).level is RiskLevel.NO_TRAVEL


# ---------------------------------------------------------------------------
# Totality: bad input must never raise (a missing reading is not a hazard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf"), "windy", True])
def test_classification_is_total(bad: object) -> None:
    """Missing, NaN, infinite and non-numeric readings degrade, never crash."""
    result = classify("wind", bad)
    assert result.level is RiskLevel.LOW
    assert result.is_missing
    assert result.data_quality == "missing"


def test_unknown_variable_raises() -> None:
    """A typo in a variable name is a programming error, not bad weather."""
    with pytest.raises(ValueError, match="Unknown weather variable"):
        classify("hail", 1.0)


# ---------------------------------------------------------------------------
# The tables themselves must tile the number line with no gaps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", sorted(BUCKETS_BY_VARIABLE))
@pytest.mark.parametrize("magnitude", [0, 1, 10, 100, 1000, 10_000, 1_000_000])
def test_buckets_tile_without_gaps(variable: str, magnitude: float) -> None:
    """Every value in a wide sweep must land in exactly one bucket.

    A gap or an overlap in the tables would make some readings silently
    unclassifiable or ambiguously classifiable.
    """
    assert find_bucket(variable, magnitude) is not None


@pytest.mark.parametrize("variable", sorted(BUCKETS_BY_VARIABLE))
def test_buckets_are_ordered_and_disjoint(variable: str) -> None:
    """Adjacent buckets must share exactly one endpoint and never overlap."""
    buckets = BUCKETS_BY_VARIABLE[variable]
    for previous, current in itertools.pairwise(buckets):
        assert previous.upper is not None
        assert current.lower is not None
        assert previous.upper == current.lower, "buckets must be contiguous"
        # The shared endpoint belongs to exactly one of the two bands.
        assert previous.upper_inclusive != current.lower_inclusive


# ---------------------------------------------------------------------------
# BE-29: golden table
# ---------------------------------------------------------------------------


def _golden_rows(path) -> list[dict[str, str]]:
    """Parse the golden table, dropping ``#`` comment lines before the header.

    The comments must be stripped *before* ``DictReader`` runs, otherwise the
    first comment line is consumed as the header row.
    """
    lines = [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def test_golden_rule_table(risk_table_csv) -> None:
    """Every cell of the published table classifies as documented."""
    rows = _golden_rows(risk_table_csv)
    assert rows, "the golden table must not be empty"

    for row in rows:
        variable, raw_value, expected_name, cell = (
            row["variable"],
            row["value"],
            row["expected_level"],
            row["table_cell"],
        )
        result = classify(variable, float(raw_value))
        assert result.level.name == expected_name, (
            f"{variable}={raw_value} (table cell {cell!r}) classified as "
            f"{result.level.name}, expected {expected_name}"
        )


def test_golden_table_covers_every_cell(risk_table_csv) -> None:
    """Guard against a truncated fixture silently reducing coverage."""
    rows = _golden_rows(risk_table_csv)
    covered = {(row["variable"], row["expected_level"]) for row in rows}
    missing = {
        (variable, level.name)
        for variable in BUCKETS_BY_VARIABLE
        for level in RiskLevel
        if (variable, level.name) not in covered
    }
    assert not missing, f"golden table is missing cells: {sorted(missing)}"
