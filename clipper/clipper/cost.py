"""Pre-run cost estimate for the agent pipeline.

Uses tiktoken for an order-of-magnitude token count and a table of published
per-MTok prices. Covers Anthropic (agent calls) and OpenAI (Whisper +
embeddings) contributions.

Prices are USD per 1,000,000 tokens (or per minute for Whisper) — update
PRICES when the published pricing changes. Caching discounts are applied
to system blocks that we know are cached across windows (rubric + brief).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .transcript import Transcript

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _ENC = None


def _tokens(text: str) -> int:
    if not text:
        return 0
    if _ENC is None:
        return max(1, len(text) // 4)
    return len(_ENC.encode(text))


# USD / MTok. input / output. Cached-input priced at 10% of input.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00, "cached": 1.50},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00, "cached": 0.30},
    "claude-haiku-4-5-20251001": {"input":  1.00, "output":  5.00, "cached": 0.10},
    # Older placeholders — add more models as needed.
    "claude-sonnet-4-5":         {"input":  3.00, "output": 15.00, "cached": 0.30},
}

WHISPER_PER_MINUTE = 0.006       # OpenAI whisper-1
EMBEDDING_PER_MTOK = 0.02        # text-embedding-3-small


@dataclass
class RoleEstimate:
    role: str
    model: str
    calls: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float

    def as_row(self) -> dict:
        return {
            "role": self.role,
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
        }


@dataclass
class CostEstimate:
    transcript_tokens: int
    duration_sec: float
    needs_transcription: bool
    roles: list[RoleEstimate] = field(default_factory=list)
    whisper_usd: float = 0.0
    embedding_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return round(self.whisper_usd + self.embedding_usd + sum(r.cost_usd for r in self.roles), 4)

    def to_dict(self) -> dict:
        return {
            "transcript_tokens": self.transcript_tokens,
            "duration_sec": round(self.duration_sec, 1),
            "needs_transcription": self.needs_transcription,
            "roles": [r.as_row() for r in self.roles],
            "whisper_usd": round(self.whisper_usd, 4),
            "embedding_usd": round(self.embedding_usd, 4),
            "total_usd": self.total_usd,
        }


def _role(role: str, model: str, calls: int, input_t: int, cached_t: int, output_t: int) -> RoleEstimate:
    p = PRICES.get(model) or PRICES["claude-sonnet-4-6"]
    cost = (
        (input_t / 1_000_000) * p["input"]
        + (cached_t / 1_000_000) * p["cached"]
        + (output_t / 1_000_000) * p["output"]
    )
    return RoleEstimate(
        role=role, model=model, calls=calls,
        input_tokens=input_t, cached_tokens=cached_t, output_tokens=output_t,
        cost_usd=cost,
    )


def estimate(
    transcript: Transcript | None,
    *,
    duration_sec: float | None = None,
    needs_transcription: bool = False,
    needs_embedding: bool = True,
    models: dict[str, str] | None = None,
    # Agent-level knobs:
    window_sec: float = 900.0,
    overlap_sec: float = 60.0,
    max_clips: int = 10,
) -> CostEstimate:
    from .produce import MODELS as DEFAULT_MODELS

    m = {**DEFAULT_MODELS, **(models or {})}
    dur = duration_sec or (transcript.duration if transcript else 0.0)

    # Condensed transcript body tokens — rough estimate: ~60 chars per segment
    # plus some timestamps.
    if transcript:
        full_body = "\n".join(seg.text.strip() for seg in transcript.segments)
        transcript_tokens = _tokens(full_body)
    else:
        # Rough: 150 words / min × 1.3 tokens/word
        transcript_tokens = int((dur / 60.0) * 150 * 1.3)

    # Number of windows (scouts).
    windows = max(1, int((dur - overlap_sec) // (window_sec - overlap_sec))) if dur else 1
    # Per-window transcript tokens (proportional share).
    window_tokens = max(100, transcript_tokens // max(1, windows))

    # Shared cached system blocks (rubric ~800 tok, brief ~600 tok, taste ~200 tok).
    SYS = 1_600

    # Producer: 1 call, full transcript in, ~500 tokens brief out.
    producer_in = transcript_tokens + 800
    producer_cached = 800
    producer_out = 600

    # Scout: N windows, each cached (rubric + brief), per-window transcript.
    scout_in = window_tokens * windows
    scout_cached = SYS * windows
    scout_out = 400 * windows

    # Editor: one call with all candidates (compact dossier, ~6000 tokens).
    editor_in = 6000
    editor_cached = SYS
    editor_out = 1500

    # Critic: one call on finalist set dossier (~1500 tokens).
    critic_in = 1500
    critic_cached = SYS
    critic_out = 600

    # Faithfulness: per clip ~700 tokens context + transcripts around.
    fa_in = 700 * max_clips
    fa_cached = SYS // 2
    fa_out = 200 * max_clips

    # Packager: one call with all clips (~500 tok each in, 500 tok each out).
    pkg_in = 500 * max_clips + SYS
    pkg_cached = SYS
    pkg_out = 500 * max_clips

    roles = [
        _role("producer",     m["producer"],     1,        producer_in, producer_cached, producer_out),
        _role("scouts",       m["scout"],        windows,  scout_in,    scout_cached,    scout_out),
        _role("editor",       m["editor"],       1,        editor_in,   editor_cached,   editor_out),
        _role("critic",       m["critic"],       1,        critic_in,   critic_cached,   critic_out),
        _role("faithfulness", m["faithfulness"], 1,        fa_in,       fa_cached,       fa_out),
        _role("packager",     m["packager"],     1,        pkg_in,      pkg_cached,      pkg_out),
    ]

    whisper_usd = (dur / 60.0) * WHISPER_PER_MINUTE if needs_transcription else 0.0
    # Embedding each final clip for semantic search.
    embedding_tokens = 1200 * max_clips if needs_embedding else 0
    embedding_usd = (embedding_tokens / 1_000_000) * EMBEDDING_PER_MTOK

    return CostEstimate(
        transcript_tokens=transcript_tokens,
        duration_sec=dur,
        needs_transcription=needs_transcription,
        roles=roles,
        whisper_usd=whisper_usd,
        embedding_usd=embedding_usd,
    )
