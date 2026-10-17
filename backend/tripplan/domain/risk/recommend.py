"""Ranking route options and explaining the result.

The assessment's preference order is::

    1. Fewest Severe miles
    2. Fewest High miles
    3. Lowest average risk
    4. Shortest travel times

One term is prepended: **fewest No Travel miles**. The published ordering never
mentions No Travel, yet a corridor containing one is not travelable at all, so
ranking it below a merely "severe" corridor is the only safe reading (D5). The
gate is a flag rather than a hardcode, so the literal four-criteria ordering can
be demonstrated on request:

>>> from tripplan.domain.risk.recommend import SAFETY_GATE_ENABLED
>>> SAFETY_GATE_ENABLED
True
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .enums import RiskLevel
from .miles import RouteRiskSummary

#: Prepend the No Travel tier to the ranking (D5).
SAFETY_GATE_ENABLED = True


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """A route option, scored but not yet ranked.

    Attributes:
        route_id: Stable identifier, unique within one analysis.
        label: Display label, e.g. ``"Option A"``.
        summary: Mileage and severity statistics from :mod:`.miles`.
        duration_s: Driving time in seconds.
        distance_m: Route length in metres, as reported by the routing provider.
    """

    route_id: str
    label: str
    summary: RouteRiskSummary
    duration_s: float
    distance_m: float

    def sort_key(self, safety_gate: bool = SAFETY_GATE_ENABLED) -> tuple[float, ...]:
        """Lexicographic ranking key.

        Lower is better. With the safety gate on, the key is::

            (no_travel_miles, severe_miles, high_miles, avg_risk, duration_s, distance_m)

        With it off, it is the assessment's literal four criteria plus the
        duration/distance tie-breaks::

            (severe_miles, high_miles, avg_risk, duration_s, distance_m)
        """
        head: tuple[float, ...] = (self.summary.no_travel_miles,) if safety_gate else ()
        return (
            *head,
            self.summary.severe_miles,
            self.summary.high_miles,
            self.summary.avg_risk,
            self.duration_s,
            self.distance_m,
        )

    @property
    def is_blocked(self) -> bool:
        """Whether this route contains any untravable miles."""
        return self.summary.no_travel_miles > 0.0


@dataclass(frozen=True, slots=True)
class Recommendation:
    """The ranking outcome.

    Attributes:
        winner: Best-ranked candidate, or ``None`` when there were no candidates.
        ranking: Candidates in rank order.
        criterion_tally: How many pairwise comparisons each candidate won, per
            criterion -- the secondary explanation form (D17).
        rationale: Headline sentences explaining the choice against the runner-up.
        all_blocked: Whether *every* candidate contains No Travel miles.
    """

    winner: RouteCandidate | None
    ranking: tuple[RouteCandidate, ...]
    criterion_tally: dict[str, dict[str, int]] = field(default_factory=dict)
    rationale: tuple[str, ...] = ()
    all_blocked: bool = False

    def rank_of(self, route_id: str) -> int | None:
        """1-based rank of ``route_id``, or ``None`` if it is not present."""
        for position, candidate in enumerate(self.ranking, start=1):
            if candidate.route_id == route_id:
                return position
        return None


#: Ranking criteria, in the order they are applied.
#:
#: ``noun`` is what gets counted, and ``better``/``worse`` complete the sentence
#: "``<amount> <comparative> <noun>``". Keeping the phrasing here rather than
#: assembling it from the criterion label is what stops the rationale reading
#: "5 no travel miles less No Travel miles than B".
CRITERIA: tuple[tuple[str, str, str, str], ...] = (
    ("no_travel_miles", "No Travel miles", "fewer", "more"),
    ("severe_miles", "Severe miles", "fewer", "more"),
    ("high_miles", "High miles", "fewer", "more"),
    ("avg_risk", "average risk", "lower", "higher"),
    ("duration_s", "travel time", "shorter", "longer"),
)


def _criterion_value(candidate: RouteCandidate, key: str) -> float:
    return {
        "no_travel_miles": candidate.summary.no_travel_miles,
        "severe_miles": candidate.summary.severe_miles,
        "high_miles": candidate.summary.high_miles,
        "avg_risk": candidate.summary.avg_risk,
        "duration_s": candidate.duration_s,
    }[key]


def _format(key: str, value: float) -> str:
    """Render a magnitude for a sentence."""
    if key == "duration_s":
        total_minutes = round(value / 60.0)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    if key == "avg_risk":
        return f"{value:.2f}"
    return f"{value:,.0f}"


def _explain_delta(winner: RouteCandidate, runner_up: RouteCandidate, key: str) -> str:
    """One clause describing how the winner beat the runner-up on ``key``.

    Empty string when the criterion is a tie, so the sentence lists only the
    things that actually decided the ranking.
    """
    a, b = _criterion_value(winner, key), _criterion_value(runner_up, key)
    if a == b:
        return ""
    _, noun, better, worse = next(c for c in CRITERIA if c[0] == key)
    comparative = better if a < b else worse
    return f"{_format(key, abs(a - b))} {comparative} {noun}"


def rank_routes(
    candidates: Sequence[RouteCandidate],
    safety_gate: bool = SAFETY_GATE_ENABLED,
) -> Recommendation:
    """Rank route options and explain the outcome.

    Args:
        candidates: The options to rank.
        safety_gate: Whether to prepend the No Travel criterion (D5).

    Returns:
        The ranking, plus both explanation forms required by D17.
    """
    if not candidates:
        return Recommendation(winner=None, ranking=(), all_blocked=False)

    ordered = tuple(sorted(candidates, key=lambda c: c.sort_key(safety_gate)))
    winner = ordered[0]

    active_criteria = tuple(c for c in CRITERIA if safety_gate or c[0] != "no_travel_miles")

    tally: dict[str, dict[str, int]] = {
        candidate.route_id: {key: 0 for key, _, _, _ in active_criteria} for candidate in ordered
    }
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            for key, _, _, _ in active_criteria:
                a, b = _criterion_value(first, key), _criterion_value(second, key)
                if a < b:
                    tally[first.route_id][key] += 1
                elif b < a:
                    tally[second.route_id][key] += 1

    rationale: list[str] = []
    if len(ordered) == 1:
        rationale.append(f"{winner.label} is the only distinct route available for this trip.")
    else:
        runner_up = ordered[1]
        deltas = [_explain_delta(winner, runner_up, key) for key, _, _, _ in active_criteria]
        stated = [d for d in deltas if d]
        rationale.append(
            f"{winner.label} ranks first over {runner_up.label}: "
            + ("; ".join(stated) if stated else "it wins on the final tie-break.")
            + "."
        )
        blocked_others = [c.label for c in ordered[1:] if c.is_blocked]
        if blocked_others:
            rationale.append(
                f"{' and '.join(blocked_others)} "
                f"{'were' if len(blocked_others) > 1 else 'was'} not viable: "
                "that route contains untravable miles."
            )

    if winner.is_blocked:
        rationale.append(
            f"Note: even the best route has "
            f"{winner.summary.no_travel_miles:,.0f} untravable miles. Treat this trip as unsafe "
            "and wait out the conditions."
        )

    return Recommendation(
        winner=winner,
        ranking=ordered,
        criterion_tally=tally,
        rationale=tuple(rationale),
        all_blocked=all(candidate.is_blocked for candidate in ordered),
    )


def blocking_warning(summary: RouteRiskSummary) -> str | None:
    """A safety warning for a summary containing No Travel miles."""
    if summary.no_travel_miles <= 0.0:
        return None
    return (
        f"{summary.no_travel_miles:,.0f} of {summary.total_miles:,.0f} miles are rated "
        f"{RiskLevel.NO_TRAVEL.label}."
    )
