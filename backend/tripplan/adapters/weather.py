"""Weather provider (Open-Meteo) and the window/location planning around it.

**The cost model drives the design.** Measured: Open-Meteo bills roughly
**4 s + 0.53 s per location**, nearly independent of how many hours are
requested, and returns HTTP 429 for parallel requests (PLAN.md §2). Two
consequences:

* Fetch **hourly series**, not single timestamps. Hours are nearly free, so one
  fetch serves the departure ETA *and* all 48 slider offsets (D7).
* Batch **sequentially** in chunks. Never fan out.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, runtime_checkable

from .errors import UpstreamError
from .http import HttpClient

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: Open-Meteo rejects ``forecast_days`` above this (measured: 17 -> HTTP 400).
MAX_FORECAST_DAYS = 16
MAX_PAST_DAYS = 92

#: Measured ceiling used to bound the location budget.
SECONDS_PER_LOCATION_ESTIMATE = 0.53

#: Requests are chunked to stay under the provider's concurrency limit.
DEFAULT_BATCH_SIZE = 20

#: Two checkpoints within this distance share one forecast fetch.
LOCATION_CELL_DEG = 0.02


@dataclass(frozen=True, slots=True)
class WeatherSeries:
    """Hourly readings at one location, in rule units.

    Attributes:
        lat: Resolved latitude the provider actually used.
        lon: Resolved longitude.
        times: Hour-start timestamps, timezone-aware UTC.
        wind_mph: Wind speed per hour, mph.
        rain_in_hr: Liquid precipitation (rain + showers), in/hr.
        snow_in_hr: Snow **depth**, in/hr -- roughly 7x the liquid equivalent
            (D15). This is what the snow thresholds are applied to.
        precipitation_in_hr: Total precipitation as liquid equivalent. Display
            context only; never a rule input.
    """

    lat: float
    lon: float
    times: tuple[datetime, ...]
    wind_mph: tuple[float | None, ...]
    rain_in_hr: tuple[float | None, ...]
    snow_in_hr: tuple[float | None, ...]
    precipitation_in_hr: tuple[float | None, ...] = ()

    def __len__(self) -> int:
        """Number of hourly slots."""
        return len(self.times)

    def reading_at(self, index: int) -> dict[str, float | None]:
        """Provider-shaped reading at ``index``, clamped to the available range."""
        if not self.times:
            msg = "empty weather series"
            raise IndexError(msg)
        i = max(0, min(index, len(self.times) - 1))
        return {
            "wind_speed_10m": self.wind_mph[i],
            "rain": self.rain_in_hr[i],
            "showers": None,  # already folded into rain_in_hr
            "snowfall": self.snow_in_hr[i],
            "precipitation": self.precipitation_in_hr[i] if self.precipitation_in_hr else None,
        }


@runtime_checkable
class WeatherProvider(Protocol):
    """Fetches hourly weather series for a set of locations."""

    def fetch(
        self,
        locations: Sequence[tuple[float, float]],
        *,
        window: ForecastWindow,
    ) -> list[WeatherSeries]:
        """Return one series per location, in the order requested.

        Raises:
            UpstreamError: The provider failed.
        """
        ...


@dataclass(frozen=True, slots=True)
class ForecastWindow:
    """The hour range a fetch must cover.

    Attributes:
        start: First hour to cover.
        end: Last hour to cover, inclusive.
    """

    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        """Number of hourly slots to request."""
        return max(1, int((self.end - self.start).total_seconds() // 3600) + 1)

    def index_of(self, moment: datetime) -> int:
        """Index of the hour containing ``moment`` (floor; see D14)."""
        delta = (moment - self.start).total_seconds() / 3600.0
        return max(0, min(math.floor(delta), self.hours - 1))


def plan_window(
    departure: datetime,
    longest_route_hours: float,
    slider_hours: int = 48,
) -> ForecastWindow:
    """Cover the departure ETAs plus every slider offset.

    The window is anchored to the departure, so no notion of "now" is needed --
    a past departure simply produces a past window, and the provider call is
    addressed with ``past_days`` (see :func:`window_fetch_params`).

    Args:
        departure: Trip departure time.
        longest_route_hours: Duration of the slowest route.
        slider_hours: Largest departure offset the UI offers.

    Returns:
        The window to fetch, snapped to the hour.
    """
    departure_utc = departure.astimezone(UTC) if departure.tzinfo else departure.replace(tzinfo=UTC)

    start = departure_utc.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=longest_route_hours + slider_hours)
    return ForecastWindow(start=start, end=end)


@dataclass(frozen=True, slots=True)
class WindowFetchParams:
    """``past_days``/``forecast_days`` needed to cover a window from today.

    Open-Meteo is addressed relative to *today*, not to an arbitrary date, so the
    window has to be expressed as an offset from now.
    """

    past_days: int
    forecast_days: int

    def validate(self) -> None:
        """Raise if the provider would reject these values.

        Raises:
            UpstreamError: The window is outside the provider's range.
        """
        if self.forecast_days > MAX_FORECAST_DAYS:
            msg = (
                f"departure is {self.forecast_days} days out, but the provider supports at most "
                f"{MAX_FORECAST_DAYS}. Choose a nearer departure date."
            )
            raise UpstreamError("open_meteo", msg, retryable=False)
        if self.past_days > MAX_PAST_DAYS:
            msg = (
                f"departure is {self.past_days} days in the past; weather history is not available."
            )
            raise UpstreamError("open_meteo", msg, retryable=False)


def window_fetch_params(
    window: ForecastWindow,
    now: datetime | None = None,
) -> WindowFetchParams:
    """Express ``window`` as provider fetch parameters."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)

    # past_days counts whole days before today that must be included.
    past_days = (
        max(0, math.ceil((today - window.start).total_seconds() / 86400))
        if (window.start < today)
        else 0
    )
    # forecast_days counts today plus the days after it that the window reaches.
    last_day = window.end.replace(hour=0, minute=0, second=0, microsecond=0)
    forecast_days = max(1, (last_day - today).days + 1)

    params = WindowFetchParams(past_days=min(past_days, MAX_PAST_DAYS), forecast_days=forecast_days)
    params.validate()
    return params


class OpenMeteoWeatherProvider:
    """Hourly forecasts from Open-Meteo, batched by location.

    Args:
        client: Shared HTTP client.
        base_url: API root.
        batch_size: Locations per request. Sequential by design (D8/R2).
    """

    def __init__(
        self,
        client: HttpClient,
        *,
        base_url: str = "https://api.open-meteo.com/v1",
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        """Configure the provider."""
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._batch_size = max(1, batch_size)

    def fetch(
        self,
        locations: Sequence[tuple[float, float]],
        *,
        window: ForecastWindow,
    ) -> list[WeatherSeries]:
        """Fetch every location, one sequential batch at a time."""
        results: list[WeatherSeries] = []
        for batch in _chunks(locations, self._batch_size):
            results.extend(self._fetch_batch(batch, window))
        return results

    def _fetch_batch(
        self,
        locations: Sequence[tuple[float, float]],
        window: ForecastWindow,
    ) -> list[WeatherSeries]:
        params = window_fetch_params(window)
        query = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in locations),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in locations),
            "hourly": ",".join(["wind_speed_10m", "rain", "showers", "snowfall", "precipitation"]),
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "snowfall_unit": "inch",
            "timezone": "UTC",
            "forecast_days": params.forecast_days,
        }
        if params.past_days:
            query["past_days"] = params.past_days

        payload = self._client.get_json(
            f"{self._base_url}/forecast", params=query, provider="open_meteo"
        )
        records = payload if isinstance(payload, list) else [payload]
        if len(records) != len(locations):
            msg = f"requested {len(locations)} locations, provider returned {len(records)}"
            raise UpstreamError("open_meteo", msg, retryable=False)

        return [_parse_series(record) for record in records]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _parse_series(record: object) -> WeatherSeries:
    """Turn one provider record into a :class:`WeatherSeries`."""
    if not isinstance(record, dict):
        msg = f"expected an object, got {type(record).__name__}"
        raise UpstreamError("open_meteo", f"malformed forecast record: {msg}", retryable=False)

    hourly = record.get("hourly")
    if not isinstance(hourly, dict):
        msg = "forecast record has no 'hourly' block"
        raise UpstreamError("open_meteo", f"malformed forecast record: {msg}", retryable=False)

    raw_times = hourly.get("time") or []
    times = tuple(_parse_time(str(t)) for t in raw_times)
    wind = _tuple(hourly.get("wind_speed_10m"))
    snow = _tuple(hourly.get("snowfall"))
    rain = _tuple(hourly.get("rain"))
    showers = _tuple(hourly.get("showers"))
    precipitation = _tuple(hourly.get("precipitation"))

    return WeatherSeries(
        lat=_as_float(record.get("latitude")),
        lon=_as_float(record.get("longitude")),
        times=times,
        wind_mph=wind,
        # Rain rule reads liquid precipitation only (D15).
        rain_in_hr=tuple(_add(a, b) for a, b in zip(rain, showers, strict=False)),
        # Snow rule reads depth.
        snow_in_hr=snow,
        precipitation_in_hr=precipitation,
    )


def _parse_time(value: str) -> datetime:
    """Parse an ISO-8601 hour stamp into an aware UTC datetime."""
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        msg = f"unparseable timestamp {value!r}"
        raise UpstreamError("open_meteo", msg, retryable=False) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _tuple(values: object) -> tuple[float | None, ...]:
    if not isinstance(values, list):
        return ()
    out: list[float | None] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(None if math.isnan(number) else number)
    return tuple(out)


def _add(a: float | None, b: float | None) -> float | None:
    if a is None and b is None:
        return None
    return (a or 0.0) + (b or 0.0)


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    """Yield consecutive slices of at most ``size`` items."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---------------------------------------------------------------------------
# location planning (BE-40)
# ---------------------------------------------------------------------------


def cell_key(lat: float, lon: float) -> tuple[int, int]:
    """Quantise a coordinate to a coarse cell.

    Overlapping routes share most of their corridor, so collapsing nearby
    checkpoints onto shared cells avoids fetching the same storm twice.
    """
    return (
        math.floor(lat / LOCATION_CELL_DEG),
        math.floor(lon / LOCATION_CELL_DEG),
    )


@dataclass(frozen=True, slots=True)
class LocationPlan:
    """Which locations to fetch, and which checkpoints map onto them.

    Attributes:
        unique: Unique ``(lat, lon)`` to fetch.
        index_of_checkpoint: Checkpoint position -> index into ``unique``.
        coarsened: Whether the spacing was reduced to respect the budget.
        requested: How many checkpoints there were before dedupe.
    """

    unique: tuple[tuple[float, float], ...]
    index_of_checkpoint: tuple[int, ...]
    coarsened: bool = False
    requested: int = 0

    @property
    def saved(self) -> int:
        """Fetches avoided by dedupe."""
        return self.requested - len(self.unique)


def plan_locations(
    checkpoints: Sequence[tuple[float, float]],
    *,
    budget: int = 90,
) -> LocationPlan:
    """Dedupe checkpoint locations into a bounded fetch list.

    Args:
        checkpoints: ``(lat, lon)`` for every checkpoint on every route.
        budget: Maximum unique locations to fetch.

    Returns:
        The plan. When the deduplicated set still exceeds ``budget``, every
        other location is kept (``coarsened=True``) so coverage degrades evenly
        instead of silently truncating the far end of the route.
    """
    if not checkpoints:
        return LocationPlan(unique=(), index_of_checkpoint=())

    cell_to_index: dict[tuple[int, int], int] = {}
    unique: list[tuple[float, float]] = []
    mapping: list[int] = []

    for lat, lon in checkpoints:
        key = cell_key(lat, lon)
        index = cell_to_index.get(key)
        if index is None:
            index = len(unique)
            cell_to_index[key] = index
            unique.append((lat, lon))
        mapping.append(index)

    coarsened = False
    if len(unique) > budget:
        coarsened = True
        before_count = len(unique)
        keep = _evenly_spaced(range(len(unique)), budget)
        unique = [unique[i] for i in keep]
        # `keep` holds positions in the *pre-filter* list; checkpoints must be
        # remapped to positions in the filtered one or they index out of range.
        position_of = {original: new for new, original in enumerate(keep)}
        # Checkpoints read from whichever retained location is nearest along the
        # corridor, so risk still varies smoothly instead of going flat.
        mapping = [position_of[_nearest_keep(old, keep)] for old in mapping]
        logger.warning(
            "weather location budget exceeded: %d unique locations reduced to %d; "
            "coverage is coarser than the selected spacing",
            before_count,
            len(unique),
        )

    return LocationPlan(
        unique=tuple(unique),
        index_of_checkpoint=tuple(mapping),
        coarsened=coarsened,
        requested=len(checkpoints),
    )


def _nearest_keep(original: int, keep: Sequence[int]) -> int:
    """Map an original index onto the nearest *retained original* index."""
    import bisect

    position = bisect.bisect_left(keep, original)
    if position == 0:
        return keep[0]
    if position >= len(keep):
        return keep[-1]
    before, after = keep[position - 1], keep[position]
    return before if (original - before) <= (after - original) else after


def _evenly_spaced(items: Iterable[int], count: int) -> list[int]:
    """Pick ``count`` values spread evenly across ``items``."""
    materialised = list(items)
    if len(materialised) <= count:
        return materialised
    step = len(materialised) / count
    return [materialised[min(len(materialised) - 1, int(i * step))] for i in range(count)]
