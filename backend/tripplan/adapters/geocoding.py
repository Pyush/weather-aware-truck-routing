"""Geocoding providers.

A protocol plus two implementations and a fallback chain. The protocol is what
lets the orchestration layer depend on "something that turns a place name into
coordinates" rather than on Nominatim specifically (D10).

Nominatim is **not** the default: measured from a datacenter IP it returns
``HTTP 403 Access denied`` (PLAN.md §2). Open-Meteo geocoding is keyless and
works, with Photon as backup.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import GeocodeNotFound
from .http import HttpClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """A resolved place.

    Attributes:
        lat: Latitude in degrees.
        lon: Longitude in degrees.
        display_name: Human-readable label for the UI.
        country_code: ISO country code, when known.
        admin1: First-level administrative region (state / province), when known.
        feature_type: Provider's classification, e.g. ``"city"``.
        source: Which provider resolved it.
    """

    lat: float
    lon: float
    display_name: str
    country_code: str | None = None
    admin1: str | None = None
    feature_type: str | None = None
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API payload."""
        return {
            "lat": self.lat,
            "lon": self.lon,
            "display_name": self.display_name,
            "country_code": self.country_code,
            "admin1": self.admin1,
            "source": self.source,
        }


@runtime_checkable
class Geocoder(Protocol):
    """Turns a free-text place into coordinates."""

    name: str

    def geocode(self, query: str) -> GeocodeResult:
        """Resolve ``query`` to exactly one place.

        Raises:
            GeocodeNotFound: Nothing matched.
            UpstreamError: The provider failed.
        """
        ...

    def suggest(self, query: str, limit: int = 5) -> Sequence[GeocodeResult]:
        """Return ranked candidates, for disambiguation."""
        ...


class OpenMeteoGeocoder:
    """Keyless geocoder at ``geocoding-api.open-meteo.com``.

    Args:
        client: Shared HTTP client.
        base_url: API root.
        country_code: Optional ISO code to restrict results.
    """

    name = "open_meteo"

    def __init__(
        self,
        client: HttpClient,
        *,
        base_url: str = "https://geocoding-api.open-meteo.com/v1",
        country_code: str | None = None,
    ) -> None:
        """Configure the provider."""
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._country_code = country_code

    def _search(self, query: str, count: int) -> list[dict[str, object]]:
        params: dict[str, object] = {
            "name": query,
            "count": count,
            "language": "en",
            "format": "json",
        }
        if self._country_code:
            params["country"] = self._country_code
        payload = self._client.get_json(
            f"{self._base_url}/search", params=params, provider=self.name
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        return list(results or [])

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Return ranked candidates for ``query``."""
        return [
            GeocodeResult(
                lat=float(row["latitude"]),
                lon=float(row["longitude"]),
                display_name=_format_name(row),
                country_code=_as_str(row.get("country_code")),
                admin1=_as_str(row.get("admin1")),
                feature_type=_as_str(row.get("feature_code")),
                source=self.name,
            )
            for row in self._search(query, limit)
        ]

    def geocode(self, query: str) -> GeocodeResult:
        """Resolve ``query``, preferring a populated place over an administrative one."""
        candidates = self.suggest(query, limit=5)
        if not candidates:
            raise GeocodeNotFound(self.name, f"no result for {query!r}", query=query)
        return _rank_candidates(candidates)[0]


class PhotonGeocoder:
    """Keyless fallback at ``photon.komoot.io``, built on OpenStreetMap data."""

    name = "photon"

    def __init__(
        self,
        client: HttpClient,
        *,
        base_url: str = "https://photon.komoot.io",
    ) -> None:
        """Configure the provider."""
        self._client = client
        self._base_url = base_url.rstrip("/")

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Return candidates for ``query``."""
        payload = self._client.get_json(
            f"{self._base_url}/api/",
            params={"q": query, "limit": limit, "lang": "en"},
            provider=self.name,
        )
        features = payload.get("features") if isinstance(payload, dict) else None
        results: list[GeocodeResult] = []
        for feature in features or []:
            geometry = feature.get("geometry") or {}
            properties = feature.get("properties") or {}
            try:
                lon, lat = float(geometry["coordinates"][0]), float(geometry["coordinates"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            results.append(
                GeocodeResult(
                    lat=lat,
                    lon=lon,
                    display_name=", ".join(
                        str(part)
                        for part in (
                            properties.get("name"),
                            properties.get("city"),
                            properties.get("state"),
                            properties.get("country"),
                        )
                        if part
                    )
                    or str(query),
                    country_code=str(properties.get("countrycode") or "")[:2] or None,
                    admin1=_as_str(properties.get("state")),
                    feature_type=_as_str(properties.get("osm_key") or properties.get("type")),
                    source=self.name,
                )
            )
        return results

    def geocode(self, query: str) -> GeocodeResult:
        """Resolve ``query`` via Photon."""
        candidates = self.suggest(query)
        if not candidates:
            raise GeocodeNotFound(self.name, f"no result for {query!r}", query=query)
        return _rank_candidates(candidates)[0]


class GeocoderChain:
    """Tries providers in order, falling through on error or an empty result.

    Args:
        providers: Ordered providers; the first is treated as primary.
    """

    def __init__(self, providers: Sequence[Geocoder]) -> None:
        """Configure the ordered provider chain."""
        if not providers:
            msg = "GeocoderChain needs at least one provider"
            raise ValueError(msg)
        self._providers = list(providers)

    @property
    def name(self) -> str:  # type: ignore[override]
        """Name of the primary provider."""
        return self._providers[0].name

    def suggest(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        """Return candidates from the first provider that answers."""
        for provider in self._providers:
            try:
                return list(provider.suggest(query, limit))
            except Exception as exc:
                logger.info("geocoder %s failed for %r: %s", provider.name, query, exc)
        return []

    def geocode(self, query: str) -> GeocodeResult:
        """Resolve ``query``, falling through the chain.

        Raises:
            GeocodeNotFound: No provider returned a result.
            UpstreamError: Every provider failed with an error (as opposed to
                simply having no match).
        """
        failures: list[Exception] = []
        for provider in self._providers:
            try:
                return provider.geocode(query)
            except GeocodeNotFound:
                # A clean "no such place" is a valid answer; keep trying others
                # but do not treat it as a failure.
                logger.info("geocoder %s had no match for %r", provider.name, query)
            except Exception as exc:
                failures.append(exc)
                logger.warning("geocoder %s failed for %r: %s", provider.name, query, exc)

        if failures and len(failures) == len(self._providers):
            raise failures[0]
        raise GeocodeNotFound(self.name, f"no provider matched {query!r}", query=query)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: Feature codes that represent a settlement rather than a region. Preferring
#: these stops "Springfield, US" from resolving to a county.
_POPULATED_CODES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLL"}


def _rank_candidates(candidates: Sequence[GeocodeResult]) -> list[GeocodeResult]:
    """Order candidates so the most specific populated place comes first."""
    return sorted(candidates, key=lambda c: (0 if c.feature_type in _POPULATED_CODES else 1,))


def _format_name(row: dict[str, object]) -> str:
    parts = [
        _as_str(row.get("name")),
        _as_str(row.get("admin1")),
        _as_str(row.get("country")),
    ]
    return ", ".join(p for p in parts if p)


def _as_str(value: object) -> str | None:
    return None if value is None else str(value)
