"""API views.

Two endpoints over one implementation:

``POST /api/v1/trips/analyze/stream/``
    NDJSON. The first events (meta, route geometry, summaries) arrive in about
    2.5 s; the checkpoint risk streams in behind them as weather resolves. This
    is what turns a 19 s upstream wait into a usable map (D8).

``POST /api/v1/trips/analyze/``
    The same payload in a single JSON body, for curl, tests and CI.

Both call :meth:`TripAnalysisService.analyze`, which drains the same generator the
streaming view consumes, so they cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from tripplan.adapters.errors import (
    GeocodeNotFound,
    NoRouteFound,
    UpstreamError,
    UpstreamRateLimited,
    UpstreamTimeout,
)
from tripplan.domain.risk.enums import level_catalog
from tripplan.domain.risk.load_rules import DEFAULT_LOAD_POLICY
from tripplan.domain.risk.recommend import AVG_RISK_PRECISION, SAFETY_GATE_ENABLED
from tripplan.domain.risk.thresholds import threshold_catalog
from tripplan.domain.risk.weather_vars import mapping_catalog
from tripplan.services.trip_analysis import ServiceRegistry, TripAnalysisService

from .errors import (
    ApiError,
    LocationNotFound,
    NoRouteAvailable,
    UpstreamUnavailable,
    ValidationFailed,
)
from .errors import (
    UpstreamTimeout as ApiUpstreamTimeout,
)
from .serializers import MAX_LOAD_LB, parse

logger = logging.getLogger(__name__)

NDJSON_CONTENT_TYPE = "application/x-ndjson"


def _service() -> TripAnalysisService:
    """Build a service from settings.

    Constructed per request rather than cached at import time so that a test can
    override settings, and so the underlying HTTP pool is not shared across
    forked workers.
    """
    return TripAnalysisService(ServiceRegistry.from_settings())


def _to_api_error(exc: Exception) -> Exception:
    """Translate an adapter failure into the API taxonomy (BE-55)."""
    if isinstance(exc, GeocodeNotFound):
        return LocationNotFound(
            f"Could not locate {exc.query!r}. Try adding a state or country.",
            details={"query": exc.query},
        )
    if isinstance(exc, NoRouteFound):
        return NoRouteAvailable(exc.message)
    if isinstance(exc, UpstreamRateLimited):
        return UpstreamUnavailable(
            "The weather service is rate limiting this project. Try again shortly.",
            details={"provider": exc.provider},
        )
    if isinstance(exc, UpstreamTimeout):
        return ApiUpstreamTimeout(
            f"The {exc.provider} service did not respond in time.",
            details={"provider": exc.provider},
        )
    if isinstance(exc, UpstreamError):
        return UpstreamUnavailable(
            f"The {exc.provider} service is unavailable.", details={"provider": exc.provider}
        )
    return exc


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class AnalyzeStreamView(APIView):
    """``POST`` an analysis request, receive NDJSON events."""

    def post(self, request: Request) -> Any:
        """Start the stream.

        Validation runs *before* the stream opens, so a bad request gets a
        normal JSON 400 with field errors rather than an error buried in NDJSON.
        """
        try:
            validated = parse(request.data if isinstance(request.data, dict) else {})
        except ValidationFailed as exc:
            # This view negotiates NDJSON for successful streams, but validation
            # happens before a stream exists. Return real JSON (including its
            # content type), not an unrendered dict through NDJSONRenderer.
            return JsonResponse(exc.to_dict(), status=exc.status_code)

        cache_key = _cache_key(validated.query)
        cached_payload = cache.get(cache_key) if _cache_enabled() else None
        if cached_payload is not None:
            logger.info("cache hit for %s", cache_key)

        service = _service()
        generator = service.stream(validated.query)

        response = StreamingHttpResponse(
            _ndjson(generator, cached_payload, validated.warnings),
            content_type=NDJSON_CONTENT_TYPE,
        )
        # Defeat proxy buffering: without this, nginx and friends hold the whole
        # body until completion and the streaming design does nothing.
        response["X-Accel-Buffering"] = "no"
        response["Cache-Control"] = "no-store"
        return response


def _ndjson(
    generator: Iterator[dict[str, Any]],
    cached_payload: dict[str, Any] | None,
    warnings: tuple[str, ...],
) -> Iterator[bytes]:
    """Encode analysis events as NDJSON, with a durable error envelope.

    An error raised *after* the stream has opened cannot change the HTTP status,
    so it is delivered as a final ``error`` event. The client must therefore
    treat every stream as ending with either ``done`` or ``error``.
    """
    if cached_payload is not None:
        # The cached meta must be replayed in full: the client reads
        # `hour_window_start` from it to index into every checkpoint's
        # `hourly_risk` array. Emitting only a `cached: true` flag would leave the
        # slider unable to resolve a single hour.
        yield _line({"type": "meta", **cached_payload.get("meta", {}), "cached": True})
        for route in cached_payload.get("routes", []):
            yield _line({"type": "route", **route})
        for entry in cached_payload.get("checkpoints", []):
            yield _line({"type": "checkpoints", **entry})
        for route, summary in zip(
            cached_payload.get("routes", []), cached_payload.get("summaries", []), strict=True
        ):
            yield _line({"type": "summary", "route_id": route["id"], **summary})
        yield _line({"type": "recommendation", **cached_payload.get("recommendation", {})})
        yield _line(
            {
                "type": "done",
                "warnings": [*warnings, *cached_payload.get("warnings", [])],
                "weather_locations": cached_payload.get("meta", {}).get("weather_locations", 0),
                "elapsed_ms": cached_payload.get("meta", {}).get("elapsed_ms", 0.0),
                "cached": True,
            }
        )
        return

    try:
        for event in generator:
            public = {k: v for k, v in event.items() if k != "analysis"}
            yield _line(public)
    except Exception as exc:
        api_error = _to_api_error(exc)
        logger.exception("analysis stream failed")
        payload = (
            api_error.to_dict()
            if hasattr(api_error, "to_dict")
            else {"error": {"code": "internal_error", "message": str(exc), "details": {}}}
        )
        yield _line({"type": "error", "status": getattr(api_error, "status_code", 500), **payload})


def _line(event: dict[str, Any]) -> bytes:
    """Serialise one NDJSON event."""
    return (json.dumps(event, default=str) + "\n").encode()


def _cache_key(query: Any) -> str:
    """A memcached-safe cache key for an analysis.

    The semantic key contains spaces, ``|`` and ``+`` from the place names and ISO
    timestamps, all of which memcached rejects outright. Hashing it keeps the key
    stable and bounded rather than merely silencing the warning.
    """
    digest = hashlib.sha256(query.cache_key().encode()).hexdigest()
    return f"tripplan:analysis:v1:{digest}"


def _cache_enabled() -> bool:
    try:
        return bool(getattr(settings, "ANALYSIS_CACHE_TTL_SECONDS", 900))
    except Exception:  # pragma: no cover - settings unavailable
        return False


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------


class AnalyzeView(APIView):
    """``POST`` an analysis request, receive the whole payload as JSON."""

    def post(self, request: Request) -> Response:
        """Run the analysis to completion.

        Returns the same ``routes`` / ``summaries`` / ``checkpoints`` /
        ``recommendation`` sections the streaming endpoint emits.
        """
        try:
            validated = parse(request.data if isinstance(request.data, dict) else {})
        except ValidationFailed as exc:
            return Response(exc.to_dict(), status=exc.status_code)

        cache_key = _cache_key(validated.query)
        use_cache = _cache_enabled()
        if use_cache:
            hit = cache.get(cache_key)
            if hit is not None:
                return Response(
                    {**hit, "meta": {**hit["meta"], "cached": True}}, status=status.HTTP_200_OK
                )

        started = time.monotonic()
        try:
            analysis = _service().analyze(validated.query)
        except Exception as exc:
            api_error = _to_api_error(exc)
            logger.exception("analysis failed")
            if isinstance(api_error, ApiError):
                return Response(api_error.to_dict(), status=api_error.status_code)
            raise

        payload = analysis.payload()
        payload["meta"]["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        payload["warnings"] = [*validated.warnings, *payload.get("warnings", [])]

        if use_cache:
            cache.set(
                cache_key,
                payload,
                int(getattr(settings, "ANALYSIS_CACHE_TTL_SECONDS", 900)),
            )

        return Response(payload, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigView(APIView):
    """Publish thresholds, load policy and level colours.

    The UI renders its legend and methodology from this endpoint, so the rules
    have exactly one definition (D12). Nothing about risk classification is
    hard-coded in the frontend.
    """

    def get(self, request: Request) -> Response:
        """Return the rule configuration."""
        policy = DEFAULT_LOAD_POLICY
        return Response(
            {
                "levels": level_catalog(),
                "thresholds": threshold_catalog(),
                "load_policy": {
                    "no_travel_wind_mph": policy.no_travel_wind_mph,
                    "heavy_load_wind_mph": policy.heavy_load_wind_mph,
                    "heavy_load_threshold_lb": policy.heavy_load_threshold_lb,
                    "extra_heavy_wind_mph": policy.extra_heavy_wind_mph,
                    "extra_heavy_threshold_lb": policy.extra_heavy_threshold_lb,
                    "boundary_inclusive": policy.boundary_inclusive,
                    "max_load_lb": MAX_LOAD_LB,
                },
                "variable_mapping": mapping_catalog(),
                "ranking": {
                    "criteria": [
                        "no_travel_miles",
                        "severe_miles",
                        "high_miles",
                        "avg_risk",
                        "duration_s",
                    ],
                    "safety_gate_enabled": SAFETY_GATE_ENABLED,
                    "avg_risk_precision": AVG_RISK_PRECISION,
                },
                "sampling": {
                    "spacings_mi": list(getattr(settings, "SAMPLE_SPACINGS_MILES", [10, 25, 50])),
                    "default_spacing_mi": getattr(settings, "DEFAULT_SAMPLE_SPACING_MILES", 25),
                },
                "slider": {
                    "max_offset_hours": getattr(settings, "SLIDER_MAX_OFFSET_HOURS", 48),
                    "semantics": "departure_offset",
                },
                "assumptions": {
                    "truck_speed_factor": getattr(settings, "TRUCK_SPEED_FACTOR", 0.85),
                    "truck_min_avg_mph": getattr(settings, "TRUCK_MIN_AVG_MPH", 25.0),
                    "truck_max_avg_mph": getattr(settings, "TRUCK_MAX_AVG_MPH", 65.0),
                    "checkpoint_time_snap": getattr(settings, "CHECKPOINT_TIME_SNAP", "floor"),
                    "weather_resolution": "hourly",
                    "note": (
                        "Precipitation and snowfall are preceding-hour sums; snowfall is snow "
                        "DEPTH, not liquid equivalent. The slider shifts the departure time."
                    ),
                },
            }
        )


class HealthView(APIView):
    """Liveness and dependency readiness."""

    def get(self, request: Request) -> JsonResponse:
        """Report service state without calling any provider."""
        return JsonResponse(
            {
                "status": "ok",
                "weather_provider": getattr(settings, "WEATHER_PROVIDER", "open_meteo"),
                "geocoder_provider": getattr(settings, "GEOCODER_PROVIDER", "open_meteo"),
            }
        )
