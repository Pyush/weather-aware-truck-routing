"""Typed upstream failures.

Every adapter raises one of these rather than leaking ``httpx`` exceptions into
the domain or API layers, so the error taxonomy in :mod:`tripplan.api.errors`
has a stable set of causes to map onto HTTP status codes (BE-55).

Note: these deliberately do **not** use ``slots=True``. A dataclass with
``slots=True`` is rebuilt as a new class object, which breaks the implicit
``__class__`` cell that zero-argument ``super()`` relies on -- instantiating any
subclass raises ``TypeError: super(type, obj): obj ... is not an instance or
subtype of type``. Exceptions gain little from slots and lose pickling.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UpstreamError(Exception):
    """Base class for a failed third-party call.

    Attributes:
        provider: Which service failed, e.g. ``"open_meteo"``.
        message: Human-readable summary.
        status_code: HTTP status, when the failure came from a response.
        retryable: Whether a later attempt might succeed.
    """

    provider: str
    message: str
    status_code: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        """Human-readable one-liner, safe to log."""
        suffix = f" (HTTP {self.status_code})" if self.status_code else ""
        return f"{self.provider}: {self.message}{suffix}"


@dataclass(frozen=True)
class UpstreamTimeout(UpstreamError):
    """The provider did not answer in time. Always retryable."""

    retryable: bool = True


@dataclass(frozen=True)
class UpstreamRateLimited(UpstreamError):
    """The provider rejected the request for rate-limit reasons."""

    retry_after_s: float | None = None
    retryable: bool = True


@dataclass(frozen=True)
class GeocodeNotFound(UpstreamError):
    """No location matched the query."""

    query: str = ""
    candidates: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        """Name the query that could not be located."""
        return f"{self.provider}: could not locate {self.query!r}"


@dataclass(frozen=True)
class AmbiguousGeocode(UpstreamError):
    """Several locations matched; the caller must disambiguate."""

    query: str = ""
    candidates: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NoRouteFound(UpstreamError):
    """The routing provider found no path between the two points."""

    origin: str = ""
    destination: str = ""
