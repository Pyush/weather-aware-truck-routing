"""Which upstream weather variable feeds which risk rule.

Kept as one table so the mapping is greppable and so ``/api/v1/config/`` can
publish it (D15).

The important, measured detail: **snow and precipitation are different physical
quantities.**

============================  ===========================================
Upstream variable             What it actually is
============================  ===========================================
``wind_speed_10m``            instantaneous wind speed, mph
``rain`` + ``showers``        liquid precipitation, in/hr (water equivalent)
``snowfall``                  snow **depth** in/hr -- roughly 7x the
                              liquid equivalent (measured 6.94-7.08 : 1)
``precipitation``             rain + showers + snow, as **liquid equivalent**
============================  ===========================================

Consequences:

* The Rain rule reads ``rain + showers``, because the rule describes rain.
* The Snow rule reads ``snowfall`` -- **depth**, which is what "snow in/hr" in
  the assessment table means. Feeding the liquid-equivalent ``precipitation``
  into the Snow rule would understate snow severity by roughly 7x: 1.4 in/hr of
  snow is HIGH, but 0.2 in/hr of liquid equivalent would classify as LOW.
* ``precipitation`` is display context only and is never a rule input.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Fields requested from the weather provider, in request order.
REQUESTED_FIELDS: tuple[str, ...] = (
    "wind_speed_10m",
    "rain",
    "showers",
    "snowfall",
    "precipitation",
)

#: Rule variables, in display order.
RULE_VARIABLES: tuple[str, ...] = ("wind", "rain", "snow")


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """How one rule variable is derived from provider fields."""

    variable: str
    fields: tuple[str, ...]
    measure: str

    def extract(self, reading: dict[str, float | None]) -> float | None:
        """Sum this variable's fields out of a provider reading.

        Returns ``None`` if every contributing field is missing. Summing rather
        than picking one field keeps the liquid-precipitation total honest when a
        provider reports only one of rain/showers.
        """
        total = 0.0
        seen = False
        for field_name in self.fields:
            raw = reading.get(field_name)
            if raw is None:
                continue
            seen = True
            total += float(raw)
        return total if seen else None


MAPPINGS: dict[str, FieldMapping] = {
    "wind": FieldMapping("wind", ("wind_speed_10m",), "instantaneous mph"),
    "rain": FieldMapping("rain", ("rain", "showers"), "liquid precipitation in/hr"),
    "snow": FieldMapping("snow", ("snowfall",), "snow depth in/hr (~7x liquid equivalent)"),
}


def reading_for(mapping: FieldMapping, reading: dict[str, float | None]) -> float | None:
    """Functional form of :meth:`FieldMapping.extract`, for use as a callback."""
    return mapping.extract(reading)


def mapping_catalog() -> list[dict[str, object]]:
    """Serialisable rule-to-variable mapping for ``GET /api/v1/config/``."""
    return [
        {
            "variable": name,
            "fields": list(mapping.fields),
            "measure": mapping.measure,
        }
        for name, mapping in MAPPINGS.items()
    ]
