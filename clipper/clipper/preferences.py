"""Learn the user's taste from their review feedback.

Everything you approve, reject, shorten, retitle, or annotate is a signal.
This module condenses the signal history into a short natural-language
"taste profile" that gets injected into the scoring system prompt on
subsequent runs, so clip suggestions learn from your editorial judgement.
"""

from __future__ import annotations

import os
from typing import Iterable

from anthropic import Anthropic

from .store import ClipRow, FeedbackRow, Store

DEFAULT_DISTILL_MODEL = "claude-sonnet-4-6"
MAX_EXAMPLES = 60


DISTILL_SYSTEM = """You are analyzing a podcast editor's clip review history to produce a short TASTE PROFILE that will be given to another AI as future guidance.

You receive a list of examples. Each example is one clip the editor reviewed, including:
- what the AI proposed (title, hook, rationale, start/end, score)
- the editor's decision: APPROVED, REJECTED, or EDITED (with the new timestamps/title)
- an optional reason

Distill the patterns into <=10 concise bullet points, in second person ("You prefer...", "You reject..."). Focus on:
- Topics / styles they approve vs reject
- How they adjust boundaries (e.g., "You tighten starts by 2-4s", "You cut sponsor mentions")
- Title style preferences
- Any explicit rules they've stated in reasons

Be specific, not generic. If there isn't enough signal for a point, omit it.

Return ONLY the bullet list, nothing else."""


def render_examples(feedback: Iterable[FeedbackRow], clip_lookup: dict[int, ClipRow]) -> str:
    lines: list[str] = []
    for f in list(feedback)[:MAX_EXAMPLES]:
        clip = clip_lookup.get(f.clip_id) if f.clip_id else None
        if not clip and f.clip_id is None:
            # A freeform note without a clip — include its reason only.
            if f.reason:
                lines.append(f"NOTE: {f.reason}")
            continue
        if not clip:
            continue
        header = (
            f"[{f.kind.upper()}] score={clip.score} "
            f"{clip.start_sec:.1f}-{clip.end_sec:.1f}s \"{clip.title}\""
        )
        lines.append(header)
        if clip.hook:
            lines.append(f"  hook: {clip.hook[:200]}")
        if clip.rationale:
            lines.append(f"  AI rationale: {clip.rationale[:200]}")
        if f.kind == "edit":
            changes = []
            if f.new_start is not None and f.original_start is not None:
                changes.append(f"start {f.original_start:.1f}->{f.new_start:.1f}")
            if f.new_end is not None and f.original_end is not None:
                changes.append(f"end {f.original_end:.1f}->{f.new_end:.1f}")
            if f.new_title and f.new_title != (f.original_title or ""):
                changes.append(f'title: "{f.original_title}" -> "{f.new_title}"')
            if changes:
                lines.append(f"  editor changed: {', '.join(changes)}")
        if f.reason:
            lines.append(f"  editor reason: {f.reason[:300]}")
    return "\n".join(lines) if lines else "(no feedback yet)"


def distill_taste_profile(store: Store, *, model: str = DEFAULT_DISTILL_MODEL) -> str:
    """Build a fresh taste profile from stored feedback and persist it."""
    feedback = store.list_feedback(limit=MAX_EXAMPLES)
    if not feedback:
        store.set_taste_profile("")
        return ""
    clip_ids = {f.clip_id for f in feedback if f.clip_id}
    clip_lookup: dict[int, ClipRow] = {}
    for cid in clip_ids:
        clip = store.get_clip(cid)
        if clip:
            clip_lookup[cid] = clip
    body = render_examples(feedback, clip_lookup)

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model=model,
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": DISTILL_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": [{"type": "text", "text": body}]}],
    )
    profile = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    store.set_taste_profile(profile)
    return profile
