"""Load-based escalation of the wind risk level.

The assessment states three load rules::

    >= 55 mph             -> No Travel, for any load
    45-54 mph + >30,000 lb -> No Travel
    35-44 mph + >40,000 lb -> Severe

Two properties are load-bearing and are asserted by the test suite:

* **Escalation only.** These rules may raise a level, never lower one. A
  downgrade path would let a heavy truck downgrade a genuinely severe wind
  reading, which the spec does not provide for and which would be unsafe (D2).
* **Strict ``>`` on the load bounds.** The table writes ``>30,000`` and
  ``>40,000``, so a truck at exactly 30,000 lb does not escalate. Industry usage
  sometimes means ``>=``; :attr:`LoadPolicy.boundary_inclusive` flips this to
  ``True`` in one place (D16).
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import RiskLevel

#: Sensible upper bound for a legal US combination rig (80,000 lb GVWR ceiling).
MAX_TRUCK_LOAD_LB = 80_000


@dataclass(frozen=True, slots=True)
class LoadPolicy:
    """The load rules, as tunable constants.

    The wind bounds reuse the Severe and High boundaries from the published
    table, so a change to the table should be mirrored here deliberately rather
    than silently drifting.
    """

    #: Wind at or above this mph is No Travel regardless of load ("*>= 55 mph*").
    no_travel_wind_mph: float = 55.0
    #: Severe-band lower bound; with :attr:`heavy_load_threshold_lb` it escalates to No Travel.
    heavy_load_wind_mph: float = 45.0
    #: Load above which the Severe band becomes No Travel.
    heavy_load_threshold_lb: int = 30_000
    #: High-band lower bound; with :attr:`extra_heavy_threshold_lb` it escalates to at least Severe.
    extra_heavy_wind_mph: float = 35.0
    #: Load above which the High band becomes at least Severe.
    extra_heavy_threshold_lb: int = 40_000
    #: Whether ``load > threshold`` is read as ``load >= threshold``.
    boundary_inclusive: bool = False

    def load_triggers(self, load_lb: float, threshold_lb: int) -> bool:
        """Whether ``load_lb`` crosses ``threshold_lb``."""
        if self.boundary_inclusive:
            return load_lb >= threshold_lb
        return load_lb > threshold_lb


DEFAULT_LOAD_POLICY = LoadPolicy()


@dataclass(frozen=True, slots=True)
class LoadAdjustment:
    """Outcome of applying the load rules to one wind classification.

    Attributes:
        level: The (possibly escalated) wind level.
        escalated: Whether any rule fired.
        rule: Identifier of the rule that fired, or ``None``.
        reason: Driver-facing explanation, or ``None``.
    """

    level: RiskLevel
    escalated: bool
    rule: str | None = None
    reason: str | None = None


def apply_load_rules(
    wind_mph: float | int | None,
    base_level: RiskLevel,
    load_lb: float,
    policy: LoadPolicy = DEFAULT_LOAD_POLICY,
) -> LoadAdjustment:
    """Apply the load rules to a wind classification.

    Args:
        wind_mph: The wind reading in mph. May be ``None`` when the upstream
            reading was missing; the load rules are then skipped and the base
            level passes through untouched.
        base_level: Wind level from :mod:`.classify`.
        load_lb: Load weight in pounds.
        policy: Rule constants.

    Returns:
        The escalated level plus the rule that produced it.
    """
    unchanged = LoadAdjustment(level=base_level, escalated=False)

    if wind_mph is None or isinstance(wind_mph, bool):
        return unchanged

    speed = float(wind_mph)
    load = float(load_lb)

    if speed >= policy.no_travel_wind_mph:
        return LoadAdjustment(
            level=RiskLevel.NO_TRAVEL,
            escalated=True,
            rule="wind_no_travel",
            reason=(
                f"Wind {speed:g} mph is at or above the {policy.no_travel_wind_mph:g} mph "
                "limit: No Travel for any load."
            ),
        )

    if speed >= policy.heavy_load_wind_mph and policy.load_triggers(
        load, policy.heavy_load_threshold_lb
    ):
        return LoadAdjustment(
            level=RiskLevel.NO_TRAVEL,
            escalated=True,
            rule="severe_wind_heavy_load",
            reason=(
                f"Wind {speed:g} mph in the "
                f"{policy.heavy_load_wind_mph:g}-{policy.no_travel_wind_mph:g} mph band with a "
                f"{load:,.0f} lb load (over {policy.heavy_load_threshold_lb:,} lb): No Travel."
            ),
        )

    if speed >= policy.extra_heavy_wind_mph and policy.load_triggers(
        load, policy.extra_heavy_threshold_lb
    ):
        # "at least Severe": an already-worse level (NO_TRAVEL) is left alone.
        escalated_level = RiskLevel.worst([base_level, RiskLevel.SEVERE])
        if escalated_level == base_level:  # pragma: no cover - base is already SEVERE+
            return LoadAdjustment(
                level=base_level,
                escalated=True,
                rule="high_wind_extra_heavy_load",
                reason=(
                    f"Wind {speed:g} mph with a {load:,.0f} lb load (over "
                    f"{policy.extra_heavy_threshold_lb:,} lb): at least Severe."
                ),
            )
        return LoadAdjustment(
            level=escalated_level,
            escalated=True,
            rule="high_wind_extra_heavy_load",
            reason=(
                f"Wind {speed:g} mph with a {load:,.0f} lb load (over "
                f"{policy.extra_heavy_threshold_lb:,} lb) escalates "
                f"{base_level.label} to at least Severe."
            ),
        )

    return unchanged
