"""Resilient Anthropic / OpenAI client factories.

All LLM traffic in the clipper goes through these so retries, backoff, and
rate-limit handling are applied centrally. Internally we wrap the SDK's
`.messages.create` / `.embeddings.create` / `.audio.transcriptions.create`
endpoints with `retry.with_retry`.
"""

from __future__ import annotations

import os
from typing import Any

from anthropic import Anthropic

from .retry import RetryPolicy, with_retry

_DEFAULT_POLICY = RetryPolicy(max_attempts=5, base_delay=2.0, backoff=2.0, max_delay=30.0)


class _RetryingCallable:
    def __init__(self, inner, policy: RetryPolicy):
        self._inner = inner
        self._policy = policy

    def create(self, *args, **kwargs):
        return with_retry(lambda: self._inner.create(*args, **kwargs), policy=self._policy)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _AnthropicResilient:
    """Thin wrapper exposing `.messages.create` with built-in retries."""

    def __init__(self, inner: Anthropic, policy: RetryPolicy):
        self._inner = inner
        self.messages = _RetryingCallable(inner.messages, policy)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _OpenAIResilient:
    def __init__(self, inner: Any, policy: RetryPolicy):
        self._inner = inner
        self.embeddings = _RetryingCallable(inner.embeddings, policy)
        if hasattr(inner, "audio"):
            self.audio = _AudioProxy(inner.audio, policy)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _AudioProxy:
    def __init__(self, inner, policy: RetryPolicy):
        self._inner = inner
        self.transcriptions = _RetryingCallable(inner.transcriptions, policy)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def anthropic_client(api_key: str | None = None, *, policy: RetryPolicy | None = None) -> _AnthropicResilient:
    inner = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    return _AnthropicResilient(inner, policy or _DEFAULT_POLICY)


def openai_client(*, policy: RetryPolicy | None = None):
    from openai import OpenAI
    return _OpenAIResilient(OpenAI(), policy or _DEFAULT_POLICY)
