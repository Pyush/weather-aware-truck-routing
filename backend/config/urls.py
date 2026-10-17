"""Root URL configuration.

The API surface is registered in Phase 3; the health check exists from Phase 0 so
the container has something to probe.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.urls import path


def health(_request: HttpRequest) -> JsonResponse:
    """Liveness probe."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", health, name="healthz"),
]
