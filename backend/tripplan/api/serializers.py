"""Request validation and the mapping to :class:`TripQuery`.

Validation exists to produce *actionable* messages. "load_lb is required" is
useless to a driver; "Load must be between 0 and 80,000 lb" tells them what to
change (BE-54).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rest_framework import serializers

from tripplan.adapters.weather import MAX_FORECAST_DAYS
from tripplan.domain.geo.eta import DEFAULT_TRUCK_PROFILE, TruckProfile
from tripplan.services.trip_analysis import TripQuery

#: US combination-rig ceiling; also the top of the assessment's load rules.
MAX_LOAD_LB = 80_000

DEFAULT_SPACINGS = (10, 25, 50)
DEFAULT_SLIDER_HOURS = 48
MAX_SLIDER_HOURS = 48


@dataclass(frozen=True, slots=True)
class ValidatedRequest:
    """A parsed request plus any non-fatal advisories."""

    query: TripQuery
    warnings: tuple[str, ...] = ()


class TripRequestSerializer(serializers.Serializer):
    """Validates the analysis request body."""

    origin = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    destination = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    departure = serializers.DateTimeField()
    load_lb = serializers.FloatField(min_value=0, max_value=MAX_LOAD_LB)
    spacing_mi = serializers.IntegerField(required=False, default=25)
    slider_hours = serializers.IntegerField(required=False, default=DEFAULT_SLIDER_HOURS)

    # Optional truck assumptions; the defaults are the documented ones (D6).
    truck_speed_factor = serializers.FloatField(
        required=False, default=DEFAULT_TRUCK_PROFILE.speed_factor, min_value=0.1, max_value=2.0
    )
    truck_min_avg_mph = serializers.FloatField(
        required=False, default=DEFAULT_TRUCK_PROFILE.min_avg_mph, min_value=1.0, max_value=120.0
    )
    truck_max_avg_mph = serializers.FloatField(
        required=False, default=DEFAULT_TRUCK_PROFILE.max_avg_mph, min_value=1.0, max_value=90.0
    )

    def validate_spacing_mi(self, value: int) -> int:
        """Only the published spacings are offered."""
        allowed = self._spacing_choices()
        if value not in allowed:
            msg = f"Spacing must be one of {', '.join(str(v) for v in allowed)} miles."
            raise serializers.ValidationError(msg)
        return value

    def validate_slider_hours(self, value: int) -> int:
        """The slider horizon is bounded by what the UI offers."""
        if value < 0 or value > MAX_SLIDER_HOURS:
            msg = f"Departure offset must be between 0 and {MAX_SLIDER_HOURS} hours."
            raise serializers.ValidationError(msg)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Cross-field checks that need the whole payload."""
        warnings: list[str] = []

        if attrs["truck_min_avg_mph"] > attrs["truck_max_avg_mph"]:
            msg = "Minimum average speed cannot exceed maximum average speed."
            raise serializers.ValidationError({"truck_min_avg_mph": msg})

        departure = attrs["departure"]
        if timezone_naive(departure):
            warnings.append(
                "No timezone was supplied, so the departure time was interpreted as UTC."
            )

        departure_utc = as_utc(departure)
        now = datetime.now(UTC)
        # A small allowance: the provider addresses days relative to today.
        if departure_utc.date() > (now + _days(MAX_FORECAST_DAYS - 1)).date():
            msg = (
                f"Forecasts are only available {MAX_FORECAST_DAYS} days ahead. "
                "Choose a nearer departure date."
            )
            raise serializers.ValidationError({"departure": msg})

        attrs["warnings"] = warnings
        return attrs

    @staticmethod
    def _spacing_choices() -> tuple[int, ...]:
        """Configured spacings, falling back to the published set."""
        try:
            from django.conf import settings

            configured = getattr(settings, "SAMPLE_SPACINGS_MILES", None)
        except Exception:  # pragma: no cover - settings unavailable outside Django
            configured = None
        return tuple(configured) if configured else DEFAULT_SPACINGS


def timezone_naive(moment: datetime) -> bool:
    """Whether a datetime lacks timezone information."""
    return moment.tzinfo is None


def as_utc(moment: datetime) -> datetime:
    """Normalise a datetime to UTC, assuming UTC when naive."""
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _days(count: int) -> timedelta:
    """A timedelta of whole days, so date arithmetic at the call site reads plainly."""
    return timedelta(days=count)


def to_query(validated: dict[str, Any]) -> TripQuery:
    """Build a :class:`TripQuery` from validated serializer data."""
    return TripQuery(
        origin=validated["origin"],
        destination=validated["destination"],
        departure=as_utc(validated["departure"]),
        load_lb=validated["load_lb"],
        spacing_mi=validated["spacing_mi"],
        slider_hours=validated["slider_hours"],
        truck=TruckProfile(
            speed_factor=validated["truck_speed_factor"],
            min_avg_mph=validated["truck_min_avg_mph"],
            max_avg_mph=validated["truck_max_avg_mph"],
        ),
    )


def departure_lacks_timezone(raw: object) -> bool:
    """Whether the *raw* departure value carries no timezone designator.

    Checked against the raw payload rather than the parsed datetime because DRF
    attaches the project's default timezone during parsing, so by the time the
    value reaches the serializer the information is already gone -- and the
    warning is meant to tell the user their input was ambiguous.
    """
    if isinstance(raw, datetime):
        return raw.tzinfo is None
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    if text.endswith(("Z", "z")):
        return False
    # A +HH:MM / -HH:MM / +HHMM offset, or a UTC abbreviation.
    tail = text[-6:]
    return not (
        (tail[0] in "+-" and ":" in tail)
        or (tail[0] in "+-" and tail[1:].replace(":", "").isdigit())
        or text.endswith(("UTC", "GMT"))
    )


def parse(payload: dict[str, Any]) -> ValidatedRequest:
    """Validate ``payload`` and return the query plus advisories.

    Raises:
        ValidationFailed: The payload is invalid.
    """
    from .errors import ValidationFailed

    serializer = TripRequestSerializer(data=payload)
    if not serializer.is_valid():
        raise ValidationFailed(
            "The request could not be validated.", details={"fields": serializer.errors}
        )
    data = dict(serializer.validated_data)
    warnings = list(data.pop("warnings", ()))

    if departure_lacks_timezone(payload.get("departure")):
        message = "No timezone was supplied, so the departure time was interpreted as UTC."
        if message not in warnings:
            warnings.append(message)

    return ValidatedRequest(query=to_query(data), warnings=tuple(warnings))
