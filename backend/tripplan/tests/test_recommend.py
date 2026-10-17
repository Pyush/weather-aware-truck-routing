"""Recommendation ranking tests (BE-27).

The ranking is *lexicographic*: a route that wins decisively on Severe miles
loses to a marginally better route only if it is also better on Severe miles.
The property tests below assert exactly that, for every route in a set of
randomly generated comparisons, by re-deriving the order from the key.
"""

from __future__ import annotations

import itertools
import random

import pytest

from tripplan.domain.risk.enums import RiskLevel as L
from tripplan.domain.risk.miles import summarise
from tripplan.domain.risk.recommend import RouteCandidate, rank_routes


def make(
    route_id: str,
    *,
    levels: list[L] | None = None,
    miles: float = 100.0,
    duration_s: float = 7200.0,
    distance_m: float = 1_619_000.0,
) -> RouteCandidate:
    """Build a candidate from a uniform level list, or a single repeated level."""
    if levels is None:
        levels = [L.LOW]
    count = len(levels)
    step = miles / count
    distances = [i * step for i in range(count)]
    return RouteCandidate(
        route_id=route_id,
        label=route_id.upper(),
        summary=summarise(distances, levels),
        duration_s=duration_s,
        distance_m=distance_m,
    )


# ---------------------------------------------------------------------------
# Each criterion, decisive on its own
# ---------------------------------------------------------------------------


def test_fewest_severe_miles_wins_first() -> None:
    """Criterion 1: 10 severe miles beats 40 severe miles despite being slower."""
    winner = make("a", levels=[L.SEVERE] * 4 + [L.LOW] * 6, duration_s=9000.0)
    loser = make("b", levels=[L.SEVERE] * 8 + [L.LOW] * 2, duration_s=3600.0)

    result = rank_routes([loser, winner])
    assert result.winner is winner
    assert result.rank_of("a") == 1
    assert result.rank_of("b") == 2


def test_fewest_high_miles_wins_when_severe_is_tied() -> None:
    """Criterion 2: Severe miles tied, fewer High miles decides."""
    winner = make("a", levels=[L.SEVERE] + [L.LOW] * 9)
    loser = make("b", levels=[L.SEVERE] + [L.HIGH] * 3 + [L.LOW] * 6)
    assert rank_routes([loser, winner]).winner is winner


def test_lowest_average_risk_wins_when_miles_tie() -> None:
    """Criterion 3: Severe and High miles tied, lowest average risk decides."""
    winner = make("a", levels=[L.LOW] * 5 + [L.MODERATE] * 5)
    loser = make("b", levels=[L.LOW] * 5 + [L.HIGH] * 2 + [L.MODERATE] * 3)
    assert rank_routes([loser, winner]).winner is winner


def test_shortest_time_wins_when_risk_ties() -> None:
    """Criterion 4: identical risk, shortest travel time decides."""
    winner = make("a", levels=[L.LOW] * 10, duration_s=7200.0)
    loser = make("b", levels=[L.LOW] * 10, duration_s=9000.0)
    assert rank_routes([loser, winner]).winner is winner


# ---------------------------------------------------------------------------
# The safety gate (D5)
# ---------------------------------------------------------------------------


def test_no_travel_miles_outrank_a_severe_route() -> None:
    """A corridor with untravable miles must never win."""
    clean = make("a", levels=[L.SEVERE] * 10, duration_s=9000.0)
    blocked = make("b", levels=[L.NO_TRAVEL] * 1 + [L.LOW] * 9, duration_s=3600.0)

    result = rank_routes([blocked, clean])
    assert result.winner is clean
    assert "untravable miles" in " ".join(result.rationale)


def test_safety_gate_can_be_disabled_to_show_the_literal_spec_order() -> None:
    """With the gate off, the published four criteria decide instead."""
    severe = make("a", levels=[L.SEVERE] * 10, duration_s=9000.0)
    blocked = make("b", levels=[L.NO_TRAVEL] * 1 + [L.LOW] * 9, duration_s=3600.0)

    gated = rank_routes([blocked, severe], safety_gate=True)
    ungated = rank_routes([blocked, severe], safety_gate=False)

    assert gated.winner is severe
    assert ungated.winner is blocked, "without the gate, severe miles decide and blocked wins"


def test_all_blocked_is_flagged() -> None:
    """Every option untravable is a distinct outcome the UI must surface."""
    result = rank_routes([make("a", levels=[L.NO_TRAVEL] * 5), make("b", levels=[L.NO_TRAVEL] * 5)])
    assert result.all_blocked
    assert result.winner is not None
    assert "untravable miles" in " ".join(result.rationale)


# ---------------------------------------------------------------------------
# Lexicographic property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("safety_gate", [True, False])
def test_ranking_matches_the_lexicographic_key(safety_gate: bool) -> None:
    """For 200 random candidate sets, the emitted order equals the sorted key."""
    rng = random.Random(4242)
    for _ in range(200):
        candidates = [
            make(
                f"r{index}",
                levels=[rng.choice(list(L)) for _ in range(rng.randint(1, 12))],
                miles=100.0,
                duration_s=rng.uniform(1000, 20000),
                distance_m=rng.uniform(1e6, 2e6),
            )
            for index in range(rng.randint(2, 6))
        ]
        result = rank_routes(candidates, safety_gate=safety_gate)
        assert [c.route_id for c in result.ranking] == [
            c.route_id for c in sorted(candidates, key=lambda c: c.sort_key(safety_gate))
        ]


def test_a_later_criterion_never_overrides_an_earlier_one() -> None:
    """The defining property of a lexicographic order, over all permutations."""
    better_on_severe = make("better_severe", levels=[L.SEVERE] * 1 + [L.LOW] * 9, duration_s=9000.0)
    worse_on_severe = make("worse_severe", levels=[L.SEVERE] * 5 + [L.LOW] * 5, duration_s=3600.0)
    assert better_on_severe.sort_key() < worse_on_severe.sort_key()

    for left, right in itertools.permutations([better_on_severe, worse_on_severe]):
        ranked = rank_routes([left, right]).ranking
        assert ranked[0].route_id == "better_severe"


# ---------------------------------------------------------------------------
# Explanation, both forms (D17)
# ---------------------------------------------------------------------------


def test_rationale_quantifies_the_difference_against_the_runner_up() -> None:
    winner = make("a", levels=[L.SEVERE] * 1 + [L.LOW] * 9, duration_s=7200.0)
    loser = make("b", levels=[L.SEVERE] * 5 + [L.LOW] * 5, duration_s=9000.0)

    rationale = " ".join(rank_routes([loser, winner]).rationale)
    assert "40 fewer Severe miles" in rationale
    assert "30m shorter" in rationale
    # The phrasing must read as a sentence, not as a label glued to a number.
    assert "no travel miles less" not in rationale
    assert "Severe miles than" not in rationale


def test_rationale_reads_as_a_sentence() -> None:
    """Regression guard for garbled comparative phrasing."""
    risky = make("a", levels=[L.NO_TRAVEL] * 1 + [L.LOW] * 9)
    clean = make("b", levels=[L.LOW] * 10)
    text = " ".join(rank_routes([risky, clean]).rationale)

    assert "fewer No Travel miles" in text
    assert "lower average risk" in text  # the clean route really is lower risk
    assert "less" not in text
    assert "more No Travel" not in text


def test_blocked_runner_up_is_called_out_as_not_viable() -> None:
    """A rejected route should say *why* it was rejected."""
    clean = make("a", levels=[L.SEVERE] * 10)
    blocked = make("b", levels=[L.NO_TRAVEL] * 1 + [L.LOW] * 9)

    rationale = " ".join(rank_routes([blocked, clean]).rationale)
    assert "untravable miles" in rationale
    assert "B was not viable" in rationale


def test_criterion_tally_counts_pairwise_wins() -> None:
    """A and b are identical, so each beats c and ties the other."""
    candidates = [
        make("a", levels=[L.LOW] * 4),
        make("b", levels=[L.LOW] * 4),
        make("c", levels=[L.HIGH] * 4),
    ]
    tally = rank_routes(candidates).criterion_tally

    # a vs b: every criterion ties, so neither scores.
    assert tally["a"]["high_miles"] == 1  # a beats c on High miles
    assert tally["a"]["severe_miles"] == 0  # nobody has any Severe miles
    assert tally["a"]["duration_s"] == 0  # identical durations
    assert tally["c"]["high_miles"] == 0  # c loses both comparisons
    assert tally["c"]["avg_risk"] == 0


def test_tally_reports_wins_per_criterion() -> None:
    fast = make("a", levels=[L.LOW] * 4, duration_s=3600.0)
    slow = make("b", levels=[L.LOW] * 4, duration_s=9000.0)
    tally = rank_routes([slow, fast]).criterion_tally
    assert tally["a"]["duration_s"] == 1
    assert tally["b"]["duration_s"] == 0


def test_noise_level_average_risk_does_not_beat_a_real_time_difference() -> None:
    """Regression guard for the live-pipeline finding.

    Two options scored 1.08 and 1.09 average risk and ranking preferred the
    *slower* one on that 0.01 gap. Here A is 0.025 safer on average but takes an
    extra hour; that difference is inside the noise of a handful of checkpoints,
    so the two must tie and travel time must decide.
    """
    marginally_safer = make("a", levels=[L.LOW] * 40, duration_s=10_800.0)  # avg 1.000
    much_faster = make("b", levels=[L.LOW] * 39 + [L.MODERATE], duration_s=7_200.0)  # avg 1.025

    assert marginally_safer.summary.avg_risk < much_faster.summary.avg_risk, "A really is safer"
    assert abs(much_faster.summary.avg_risk - marginally_safer.summary.avg_risk) < 0.05, "barely"

    result = rank_routes([marginally_safer, much_faster])
    assert result.winner is much_faster, "a noise-level risk gap must not outrank an hour"
    assert "1h 00m shorter travel time" in " ".join(result.rationale)
    assert "average risk" not in " ".join(result.rationale), "risk is a tie, not a reason"


def test_genuinely_lower_average_risk_still_wins_over_time() -> None:
    """The tolerance must not swallow a real difference."""
    clearly_safer = make("a", levels=[L.LOW] * 10, duration_s=14_400.0)  # avg 1.0
    riskier = make("b", levels=[L.LOW] * 5 + [L.MODERATE] * 5, duration_s=7_200.0)  # avg 1.5

    assert clearly_safer.summary.avg_risk == pytest.approx(1.0)
    assert riskier.summary.avg_risk == pytest.approx(1.5)

    result = rank_routes([clearly_safer, riskier])
    assert result.winner is clearly_safer, "a real 0.5 risk gap outranks two hours"
    assert "lower average risk" in " ".join(result.rationale)


def test_single_candidate_is_explained_without_a_runner_up() -> None:
    result = rank_routes([make("only", levels=[L.LOW])])
    assert result.winner is not None
    assert "only distinct route" in " ".join(result.rationale)


def test_no_candidates_yields_no_winner() -> None:
    result = rank_routes([])
    assert result.winner is None
    assert result.ranking == ()
    assert result.rank_of("a") is None
