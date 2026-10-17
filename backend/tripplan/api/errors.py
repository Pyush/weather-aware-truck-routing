"""API error taxonomy.

Maps upstream and validation failures onto stable HTTP codes so the frontend can
render a useful message instead of a stack trace (BE-55).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from rest_framework.response import Response


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


def api_exception_handler(exc: Exception, context: dict[str, object]) -> Response | None:
    """DRF exception hook.

    Converts :class:`ApiError` into a structured response and defers everything
    else to DRF's default handler.
    """
    from rest_framework.response import Response
    from rest_framework.views import exception_handler

    if isinstance(exc, ApiError):
        return Response(exc.to_dict(), status=exc.status_code)
    return exception_handler(exc, context)
