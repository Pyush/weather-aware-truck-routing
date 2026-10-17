"""API error taxonomy.

Maps failures onto stable HTTP codes so the frontend can render a useful message
instead of a stack trace (BE-55). Each class carries a machine-readable ``code``
that the UI switches on -- the copy is allowed to change, the code is not.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Base class for errors that carry an HTTP status and a stable code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """Build an error with a stable code and optional structured details."""
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Render the error as an API payload."""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ValidationFailed(ApiError):
    """The request body did not pass validation."""

    status_code = 400
    code = "validation_failed"


class LocationNotFound(ApiError):
    """An origin or destination could not be resolved."""

    status_code = 404
    code = "location_not_found"


class NoRouteAvailable(ApiError):
    """No route exists between the resolved endpoints."""

    status_code = 422
    code = "no_route"


class DepartureOutOfRange(ApiError):
    """The departure date is beyond the forecast horizon of the weather provider."""

    status_code = 400
    code = "departure_out_of_range"


class UpstreamUnavailable(ApiError):
    """A third-party provider failed and no partial result was possible."""

    status_code = 502
    code = "upstream_unavailable"


class UpstreamTimeout(ApiError):
    """A third-party provider did not respond in time."""

    status_code = 504
    code = "upstream_timeout"


class ServiceUnavailable(ApiError):
    """Every configured provider is unavailable."""

    status_code = 503
    code = "service_unavailable"


def api_exception_handler(exc: Exception, context: dict[str, object]) -> Any:
    """DRF exception hook.

    Converts :class:`ApiError` into a structured response and defers everything
    else to DRF's default handler.
    """
    from rest_framework.response import Response
    from rest_framework.views import exception_handler

    if isinstance(exc, ApiError):
        return Response(exc.to_dict(), status=exc.status_code)
    return exception_handler(exc, context)
