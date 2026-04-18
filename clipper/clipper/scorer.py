"""Claude-powered clip scoring with prompt caching."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from anthropic import Anthropic

from .transcript import Segment, Transcript, format_timestamp

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_CLIPS_PER_WINDOW = 8
TARGET_CLIP_SECONDS = (30, 120)

SYSTEM_PROMPT = """You are a world-class podcast producer who picks short clips that perform on TikTok, Reels, Shorts, and Twitter/X.

You will receive a transcript window with timestamps. Select the segments most worth clipping. Be selective — only return clips that meet the bar.

Great clips have at least several of these:
- A strong hook in the first 3 seconds (a provocative claim, question, or vivid image)
- A self-contained idea that lands without external context
- Emotional charge: surprise, disagreement, humor, vulnerability, awe, or moral stakes
- A clear payoff or punchline — the listener leaves with something
- Quotable phrasing; concrete examples beat abstractions
- Minimal crosstalk and filler

Avoid:
- Pure setup without payoff, or payoff without setup
- Inside jokes, references that require prior context from earlier in the episode
- Sponsor reads, intros, outros
- Rambling tangents
- Anything that ends mid-thought

Duration: 30-120 seconds each. Shorter is better when the idea is tight.

For each clip return:
- start: timestamp in seconds (float) — use the timestamp of the first word that should appear
- end: timestamp in seconds (float) — the timestamp right after the last word
- title: a punchy <=60 char title optimized for a feed
- hook: the first sentence spoken in the clip (verbatim)
- rationale: one sentence on why this will perform
- score: 1-100, your honest virality prediction
- tags: 2-5 short topical tags

Return ONLY a JSON object: {"clips": [...]}. No prose outside the JSON."""


@dataclass
class ClipCandidate:
    video_slug: str
    start: float
    end: float
    title: str
    hook: str
    rationale: str
    score: int
    tags: list[str] = field(default_factory=list)
    transcript_excerpt: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return asdict(self)


def _render_window(transcript: Transcript) -> str:
    lines = []
    for seg in transcript.segments:
        lines.append(f"[{format_timestamp(seg.start)} - {format_timestamp(seg.end)}] {seg.text.strip()}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Handle ```json fences or extra prose around the JSON.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    brace = text.find("{")
    last = text.rfind("}")
    if brace >= 0 and last > brace:
        return json.loads(text[brace:last + 1])
    raise ValueError(f"Could not find JSON object in model output: {text[:200]}")


def _snap_to_words(start: float, end: float, segments: Iterable[Segment]) -> tuple[float, float]:
    """If word-level timings exist, snap boundaries to the nearest word boundary."""
    words = [w for s in segments for w in s.words]
    if not words:
        return start, end
    start_word = min(words, key=lambda w: abs(w.start - start))
    end_word = min(words, key=lambda w: abs(w.end - end))
    return start_word.start, max(end_word.end, start_word.start + 1.0)


def _excerpt(segments: Iterable[Segment], start: float, end: float, max_chars: int = 1200) -> str:
    chunks: list[str] = []
    for seg in segments:
        if seg.end < start or seg.start > end:
            continue
        chunks.append(seg.text.strip())
        if sum(len(c) for c in chunks) > max_chars:
            break
    text = " ".join(chunks)
    return text[:max_chars]


def score_transcript(
    transcript: Transcript,
    *,
    video_slug: str,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_clips: int = 10,
    min_score: int = 70,
) -> list[ClipCandidate]:
    """Ask Claude to propose clips across the full transcript, deduped and ranked."""
    client = client or Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    windows = transcript.windowed(window_sec=900.0, overlap_sec=60.0) or [transcript]

    raw: list[ClipCandidate] = []
    for window in windows:
        body = _render_window(window)
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Transcript window (episode: {video_slug}).\n"
                                f"Return up to {MAX_CLIPS_PER_WINDOW} clips, duration "
                                f"{TARGET_CLIP_SECONDS[0]}-{TARGET_CLIP_SECONDS[1]}s each.\n\n"
                                f"{body}"
                            ),
                        }
                    ],
                }
            ],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        try:
            data = _extract_json(text)
        except ValueError:
            continue
        for clip in data.get("clips", []):
            try:
                start, end = _snap_to_words(
                    float(clip["start"]), float(clip["end"]), window.segments
                )
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 5:
                continue
            raw.append(
                ClipCandidate(
                    video_slug=video_slug,
                    start=start,
                    end=end,
                    title=str(clip.get("title", "")).strip()[:120],
                    hook=str(clip.get("hook", "")).strip()[:300],
                    rationale=str(clip.get("rationale", "")).strip()[:400],
                    score=int(clip.get("score", 0)),
                    tags=[str(t)[:40] for t in (clip.get("tags") or [])][:6],
                    transcript_excerpt=_excerpt(transcript.segments, start, end),
                )
            )

    raw.sort(key=lambda c: c.score, reverse=True)
    return _dedupe(raw, min_score=min_score)[:max_clips]


def _dedupe(candidates: list[ClipCandidate], *, min_score: int) -> list[ClipCandidate]:
    """Drop clips that overlap a higher-scoring clip by more than 50%."""
    kept: list[ClipCandidate] = []
    for cand in candidates:
        if cand.score < min_score:
            continue
        overlaps = False
        for k in kept:
            inter = max(0.0, min(cand.end, k.end) - max(cand.start, k.start))
            if inter / max(cand.duration, 1.0) > 0.5:
                overlaps = True
                break
        if not overlaps:
            kept.append(cand)
    return kept
