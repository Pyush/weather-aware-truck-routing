"""Shared HTTP client for upstream providers.

One pooled :class:`httpx.Client`, one retry policy, one error vocabulary.

**Retries are deliberately conservative.** Measured against Open-Meteo: six
concurrent requests earn an immediate HTTP 429, while sequential requests in
batches of ~20 are accepted (PLAN.md §2). So this client does *sequential* work
and retries only 429/5xx with exponential backoff, honouring ``Retry-After``.
Adapters batch; they never fan out.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from .errors import UpstreamError, UpstreamRateLimited, UpstreamTimeout

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "weather-aware-truck-routing/0.1 (assessment project; contact via repository)"

#: Only these statuses are worth a second attempt. A 400 means the request is
#: malformed and will stay malformed.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpClient:
    """A thin retrying wrapper around a pooled ``httpx.Client``.

    Args:
        timeout_s: Per-request timeout.
        max_retries: Additional attempts after the first.
        backoff_base_s: First backoff delay; doubles per attempt.
        backoff_cap_s: Ceiling on a single backoff delay.
        client: Inject a pre-built client (tests). When omitted, one is created
            lazily so importing this module performs no I/O.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        backoff_cap_s: float = 8.0,
        user_agent: str = DEFAULT_USER_AGENT,
        client: httpx.Client | None = None,
    ) -> None:
        """Configure the client. A pooled client is created lazily."""
        self._timeout_s = timeout_s
        self._max_retries = max(0, max_retries)
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )

    @classmethod
    def from_settings(cls) -> HttpClient:
        """Build a client from Django settings."""
        from django.conf import settings

        return cls(
            timeout_s=float(getattr(settings, "OPEN_METEO_TIMEOUT_SECONDS", 45.0)),
            max_retries=int(getattr(settings, "HTTP_MAX_RETRIES", 3)),
        )

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        provider: str = "upstream",
    ) -> Any:
        """GET ``url`` and decode JSON, retrying transient failures.

        Args:
            url: Absolute URL.
            params: Query parameters.
            provider: Name used in error messages and logs.

        Returns:
            The decoded JSON body.

        Raises:
            UpstreamRateLimited: Retries exhausted against a rate limiter.
            UpstreamTimeout: The provider did not respond in time.
            UpstreamError: Any other non-retryable or exhausted failure.
        """
        last: UpstreamError | None = None

        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(self._backoff_delay(attempt, last))

            started = time.monotonic()
            try:
                response = self._client.get(url, params=dict(params or {}))
            except httpx.TimeoutException as exc:
                last = UpstreamTimeout(provider, f"request timed out: {exc}")
                logger.warning("%s timeout (attempt %d)", provider, attempt + 1)
                continue
            except httpx.HTTPError as exc:
                last = UpstreamError(provider, f"transport error: {exc}")
                logger.warning("%s transport error (attempt %d)", provider, attempt + 1)
                continue

            elapsed_ms = (time.monotonic() - started) * 1000
            logger.info(
                "%s %s -> %d in %.0fms (attempt %d)",
                provider,
                url.split("?")[0],
                response.status_code,
                elapsed_ms,
                attempt + 1,
            )

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                last = UpstreamRateLimited(
                    provider,
                    "rate limited",
                    status_code=429,
                    retry_after_s=retry_after,
                )
                continue

            if response.status_code in RETRYABLE_STATUS:
                last = UpstreamError(
                    provider,
                    f"upstream returned {response.status_code}",
                    status_code=response.status_code,
                    retryable=True,
                )
                continue

            if response.is_error:
                # 4xx other than 429: the request itself is wrong. Retrying is
                # pointless and wastes the provider's rate-limit budget.
                raise UpstreamError(
                    provider,
                    f"upstream rejected the request ({response.status_code})",
                    status_code=response.status_code,
                    retryable=False,
                )

            try:
                return response.json()
            except ValueError as exc:
                raise UpstreamError(
                    provider, f"response was not valid JSON: {exc}", retryable=False
                ) from exc

        raise last or UpstreamError(provider, "request failed with no diagnostic")

    def _backoff_delay(self, attempt: int, last: UpstreamError | None) -> float:
        """Delay before ``attempt``, honouring ``Retry-After`` when present."""
        if isinstance(last, UpstreamRateLimited) and last.retry_after_s is not None:
            return min(last.retry_after_s, self._backoff_cap_s)
        # Exponential backoff with full jitter, so concurrent callers do not
        # resynchronise into a thundering herd.
        ceiling = min(self._backoff_base_s * (2 ** (attempt - 1)), self._backoff_cap_s)
        return random.uniform(0.0, ceiling)

    def close(self) -> None:
        """Close the underlying client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        """Support ``with`` so the pooled client is always closed."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the client on exit."""
        self.close()


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        # HTTP-date form is not used by these providers; ignore it rather than
        # guessing a delay.
        return None
    return max(seconds, 0.0)
