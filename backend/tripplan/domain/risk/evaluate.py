"""Evaluating the overall risk at a single checkpoint.

A checkpoint has one overall level: the worst of wind, rain and snow (D3). This
module also records *why* -- which variable drove the verdict, and which rules
fired -- because the UI shows that reasoning in map tooltips and the README
reproduces it in a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classify import Classification, classify
from .enums import RiskLevel
from .load_rules import DEFAULT_LOAD_POLICY, LoadAdjustment, LoadPolicy, apply_load_rules
from .weather_vars import MAPPINGS, RULE_VARIABLES


@dataclass(frozen=True, slots=True)
class CheckpointRisk:
    """Risk assessment for one point on a route.

    Attributes:
        level: Overall level -- worst of the three variables after load
            escalation.
        wind: Wind classification, after load escalation.
        rain: Rain classification.
        snow: Snow classification.
        dominant_factor: Which variable set the overall level. Ties break in the
            order wind > snow > rain (wind is the hazard the load rules treat as
            the dangerous one).
        reasons: Human-readable strings explaining the verdict.
        data_quality: ``"ok"``, or ``"partial"``/``"missing"`` when the upstream
            reading was unusable.
    """

    level: RiskLevel
    wind: Classification
    rain: Classification
    snow: Classification
    dominant_factor: str
    reasons: tuple[str, ...]
    data_quality: str

    @property
    def classifications(self) -> dict[str, Classification]:
        """Per-variable classifications, keyed by variable name."""
        return {"wind": self.wind, "rain": self.rain, "snow": self.snow}

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "level": self.level.name,
            "level_value": int(self.level),
            "dominant_factor": self.dominant_factor,
            "data_quality": self.data_quality,
            "reasons": list(self.reasons),
            "wind": _classification_dict(self.wind),
            "rain": _classification_dict(self.rain),
            "snow": _classification_dict(self.snow),
            "load_escalation": self.wind.bucket_label,
        }


def _classification_dict(classification: Classification) -> dict[str, object]:
    return {
        "level": classification.level.name,
        "value": classification.value,
        "unit": classification.unit,
        "bucket": classification.bucket_label,
        "data_quality": classification.data_quality,
    }


def _pick_dominant(
    wind: Classification,
    snow: Classification,
    rain: Classification,
    level: RiskLevel,
) -> str:
    """Name the variable responsible for ``level``.

    Ties resolve wind > snow > rain, matching the order the load rules treat
    wind as the escalating hazard.
    """
    for name, classification in (("wind", wind), ("snow", snow), ("rain", rain)):
        if classification.level == level:
            return name
    return "wind"  # pragma: no cover - unreachable while level == max(levels)


def _quality(wind: Classification, rain: Classification, snow: Classification) -> str:
    missing = [c for c in (wind, rain, snow) if c.is_missing]
    if len(missing) == 3:
        return "missing"
    return "partial" if missing else "ok"


def evaluate_checkpoint(
    reading: dict[str, float | None],
    load_lb: float,
    policy: LoadPolicy = DEFAULT_LOAD_POLICY,
) -> CheckpointRisk:
    """Classify one checkpoint's weather and apply the load rules.

    Args:
        reading: Provider fields for this point, e.g.
            ``{"wind_speed_10m": 48.0, "rain": 0.0, "showers": 0.0, "snowfall": 0.1}``.
            Unknown keys are ignored; missing keys are tolerated.
        load_lb: Load weight in pounds.
        policy: Load rule constants.

    Returns:
        The full assessment, including the reasoning strings.
    """
    classified = {
        variable: classify(variable, MAPPINGS[variable].extract(reading))
        for variable in RULE_VARIABLES
    }

    wind_raw = classified["wind"]
    rain = classified["rain"]
    snow = classified["snow"]

    # Load rules act on wind only; rain and snow have no load interaction in the
    # assessment's table.
    adjustment: LoadAdjustment = apply_load_rules(wind_raw.value, wind_raw.level, load_lb, policy)

    wind = (
        wind_raw
        if not adjustment.escalated
        else Classification(
            variable="wind",
            level=adjustment.level,
            bucket_label=f"{wind_raw.bucket_label} -> {adjustment.level.label} (load)",
            value=wind_raw.value,
            unit=wind_raw.unit,
            data_quality=wind_raw.data_quality,
        )
    )

    level = RiskLevel.worst([wind.level, rain.level, snow.level])
    reasons: list[str] = []

    if adjustment.reason:
        reasons.append(adjustment.reason)

    for classification in (rain, snow):
        if classification.is_missing:
            continue
        if classification.level == level and classification.level > RiskLevel.LOW:
            reasons.append(
                f"{classification.variable.capitalize()} "
                f"{classification.value:g} {classification.unit} is "
                f"{classification.level.label}."
            )

    if not reasons:
        reasons.append(f"All conditions {level.label.lower()}.")

    if level == wind.level and level > RiskLevel.LOW and not adjustment.reason:
        reasons.append(f"Wind {wind.value:g} mph is {wind.level.label}.")

    return CheckpointRisk(
        level=level,
        wind=wind,
        rain=rain,
        snow=snow,
        dominant_factor=_pick_dominant(wind, snow, rain, level),
        reasons=tuple(dict.fromkeys(reasons)),  # de-duplicate, keep order
        data_quality=_quality(wind, rain, snow),
    )


@dataclass(frozen=True, slots=True)
class WeatherReading:
    """One point in time at one location, in the rule variables' units.

    Constructed from provider fields via :func:`from_reading` so that the units
    are normalised once, at the edge, rather than inside the rules.
    """

    wind_mph: float | None = None
    rain_in_hr: float | None = None
    snow_in_hr: float | None = None
    extra: dict[str, float | None] = field(default_factory=dict, compare=False)

    @classmethod
    def from_reading(cls, reading: dict[str, float | None]) -> WeatherReading:
        """Build a reading from raw provider fields."""
        return cls(
            wind_mph=MAPPINGS["wind"].extract(reading),
            rain_in_hr=MAPPINGS["rain"].extract(reading),
            snow_in_hr=MAPPINGS["snow"].extract(reading),
            extra={
                key: value
                for key, value in reading.items()
                if key not in MAPPINGS["wind"].fields
                and key not in MAPPINGS["rain"].fields
                and key not in MAPPINGS["snow"].fields
            },
        )

    def evaluate(self, load_lb: float, policy: LoadPolicy = DEFAULT_LOAD_POLICY) -> CheckpointRisk:
        """Evaluate this reading against the rules."""
        return evaluate_checkpoint(
            {
                "wind_speed_10m": self.wind_mph,
                "rain": self.rain_in_hr,
                "showers": None,
                "snowfall": self.snow_in_hr,
            },
            load_lb,
            policy,
        )
