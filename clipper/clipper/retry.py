"""Retry with exponential backoff for LLM + embedding calls.

Catches rate-limit, overload, and transient network errors from the Anthropic
and OpenAI SDKs (and stdlib urllib) and retries with jittered exponential
backoff, respecting Retry-After when present.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0        # seconds
    max_delay: float = 30.0
    backoff: float = 2.0
    jitter: float = 0.25           # 0..1 — fraction of delay

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            # Honor the server's hint directly — clamp only to prevent absurd values.
            return max(0.0, min(float(retry_after), self.max_delay * 2))
        raw = self.base_delay * (self.backoff ** (attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter > 0:
            raw *= 1.0 + random.uniform(-self.jitter, self.jitter)
        return max(0.1, raw)


def _retry_after(exc: Exception) -> float | None:
    """Pull a Retry-After hint from common SDK exception shapes."""
    for attr in ("response", "resp"):
        r = getattr(exc, attr, None)
        if r is not None:
            headers = getattr(r, "headers", None) or {}
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    return float(ra)
                except (TypeError, ValueError):
                    return None
    return None


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    # Anthropic SDK
    if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "APIStatusError"):
        status = getattr(exc, "status_code", None)
        if status is None:
            return True
        return status in (408, 409, 425, 429, 500, 502, 503, 504, 529)  # 529 = overloaded
    # OpenAI SDK
    if name in ("RateLimitError", "APIError", "APIConnectionError", "Timeout",
                "InternalServerError", "APIStatusError", "APITimeoutError"):
        return True
    # stdlib urllib (used by our Postiz client)
    if "URLError" in name:
        return True
    # HTTPError on stdlib urllib — check 5xx/429
    if "HTTPError" in name:
        code = getattr(exc, "code", 500)
        return code in (408, 409, 425, 429, 500, 502, 503, 504)
    return False


def with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> T:
    """Execute `fn`, retrying transient failures with exponential backoff.

    `on_retry(attempt, sleep_seconds, exc)` is called before each sleep so
    callers can log to the jobs drawer.
    """
    policy = policy or RetryPolicy()
    last_exc: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt >= policy.max_attempts:
                raise
            delay = policy.delay(attempt, retry_after=_retry_after(exc))
            if on_retry:
                try:
                    on_retry(attempt, delay, exc)
                except Exception:
                    pass
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def retrying(policy: RetryPolicy | None = None):
    """Decorator form."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs) -> T:
            return with_retry(lambda: func(*args, **kwargs), policy=policy)
        wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return wrapper
    return decorator
