"""Deterministic synthetic weather provider.

Purpose-built rather than a stub that returns zeros, for three reasons:

* **CI and the Loom recording must not depend on a third party.** If the demo
  breaks because Open-Meteo rate-limits us mid-recording, the deliverable is
  damaged.
* **The interesting cases are rare.** A keyless forecast rarely contains a
  blizzard, so the Severe / No Travel paths would never be exercised end to end.
* **It must be reproducible.** Seeding on the location and hour means the same
  trip always produces the same storm, so a screenshot in the README matches
  what a reviewer sees.

The synthetic model is deliberately simple and legible: a Gaussian storm band in
latitude crossed with a Gaussian in time, plus a deterministic per-location
jitter so neighbouring checkpoints differ (real weather is not constant along a
corridor, and a mock that returned identical readings everywhere would hide bugs
in the per-checkpoint plumbing).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .weather import ForecastWindow, WeatherSeries


@dataclass(frozen=True, slots=True)
class MockWeatherConfig:
    """Shape of the synthetic storm.

    Attributes:
        storm_center_lat: Latitude the storm band is centred on.
        storm_lat_width_deg: Standard deviation of the band, in degrees.
        storm_center_hour: Hour offset into the window where the storm peaks.
        storm_hour_width: Standard deviation in hours.
        peak_wind_mph: Wind at the very centre of the band at the peak hour.
        peak_rain_in_hr: Rain rate at the centre at the peak hour.
        snow_latitude: North of this latitude, precipitation falls as snow.
        base_wind_mph: Background wind away from the storm.
        jitter_mph: Per-location deterministic variation, so points are not equal.
        snow_fraction: Share of precipitation falling as snow in the cold band.
    """

    storm_center_lat: float = 41.0
    storm_lat_width_deg: float = 2.5
    storm_center_hour: int = 14
    storm_hour_width: float = 7.0
    peak_wind_mph: float = 58.0
    peak_rain_in_hr: float = 1.4
    snow_latitude: float = 44.0
    base_wind_mph: float = 9.0
    jitter_mph: float = 6.0
    snow_fraction: float = 0.8


@dataclass(frozen=True, slots=True)
class MockWeatherProvider:
    """A :class:`~tripplan.adapters.weather.WeatherProvider` with no I/O.

    Satisfies the same protocol as the Open-Meteo provider, so the orchestration
    layer cannot tell the difference -- which is what makes the demo safe.
    """

    config: MockWeatherConfig = field(default_factory=MockWeatherConfig)

    #: Identifies this provider in payloads and logs.
    name: str = "mock"

    def fetch(
        self,
        locations: Sequence[tuple[float, float]],
        *,
        window: ForecastWindow,
    ) -> list[WeatherSeries]:
        """Generate one deterministic series per location."""
        return [self._series(lat, lon, window) for lat, lon in locations]

    # -- generation ---------------------------------------------------------

    def _series(self, lat: float, lon: float, window: ForecastWindow) -> WeatherSeries:
        times = tuple(window.start + timedelta(hours=i) for i in range(window.hours))
        winds: list[float | None] = []
        rains: list[float | None] = []
        snows: list[float | None] = []
        precipitation: list[float | None] = []

        for hour_index in range(window.hours):
            intensity = self._intensity(lat, lon, hour_index)
            wind = self.config.base_wind_mph + self.config.peak_wind_mph * intensity
            rain = self.config.peak_rain_in_hr * intensity
            # Snow in the cold latitudes; the storm tapers as it converts.
            snow_share = self.config.snow_fraction if lat >= self.config.snow_latitude else 0.0
            snow = rain * snow_share
            rain = rain * (1.0 - snow_share)

            winds.append(round(wind, 2))
            rains.append(round(rain, 4))
            snows.append(round(snow, 4))
            precipitation.append(round(rain + snow, 4))

        return WeatherSeries(
            lat=lat,
            lon=lon,
            times=times,
            wind_mph=tuple(winds),
            rain_in_hr=tuple(rains),
            snow_in_hr=tuple(snows),
            precipitation_in_hr=tuple(precipitation),
        )

    def _intensity(self, lat: float, lon: float, hour_index: int) -> float:
        """Storm intensity in ``[0, 1]`` at a location and hour."""
        cfg = self.config
        lat_term = math.exp(-0.5 * ((lat - cfg.storm_center_lat) / cfg.storm_lat_width_deg) ** 2)
        hour_term = math.exp(
            -0.5 * ((hour_index - cfg.storm_center_hour) / cfg.storm_hour_width) ** 2
        )
        return max(0.0, min(1.0, lat_term * hour_term * self._jitter(lat, lon)))

    @staticmethod
    def _jitter(lat: float, lon: float) -> float:
        """Deterministic per-location factor in roughly ``[0.75, 1.15]``.

        Derived from the coordinates via a stable hash rather than ``random``,
        so the same location always gets the same offset across processes.
        """
        key = f"{lat:.4f}:{lon:.4f}".encode()
        digest = hashlib.sha256(key).digest()
        fraction = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        return 0.75 + fraction * 0.4


class ClearWeatherProvider(MockWeatherProvider):
    """A mock with the storm switched off, for calm-weather demos."""

    def __init__(self) -> None:
        """Build the calm-weather variant."""
        super().__init__(
            config=MockWeatherConfig(
                peak_wind_mph=0.0,
                peak_rain_in_hr=0.0,
                snow_fraction=0.0,
                base_wind_mph=7.0,
                jitter_mph=4.0,
            )
        )


def provider_from_name(name: str) -> MockWeatherProvider | None:
    """Build a mock provider by name, or ``None`` if ``name`` is not a mock.

    Lets ``WEATHER_PROVIDER=mock`` and ``WEATHER_PROVIDER=mock_clear`` share the
    Open-Meteo provider's configuration slot.
    """
    if name == "mock":
        return MockWeatherProvider()
    if name in {"mock_clear", "clear"}:
        return ClearWeatherProvider()
    return None


def _utc_now() -> datetime:
    """Current UTC time (used only for logging context)."""
    return datetime.now(UTC)
