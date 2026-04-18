"""Coordinator: orchestrates the Producer → Scouts → Editor → Packager → Critic pipeline.

Entry point: `produce_clips(transcript, meta, ...)`. Returns a ProductionResult
containing the EpisodeBrief, the final ClipCandidates (refined + critic-gated)
and the ClipPackage list.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from anthropic import Anthropic

from .agents import (
    ClipPackage,
    CriticResult,
    EpisodeBrief,
    run_critic,
    run_editor,
    run_packager,
    run_producer,
    run_scout_on_window,
)
from .ingester import SourceMeta
from .scorer import ClipCandidate
from .transcript import Transcript


MODELS = {
    "producer": "claude-opus-4-7",
    "scout": "claude-sonnet-4-6",
    "editor": "claude-opus-4-7",
    "packager": "claude-sonnet-4-6",
    "critic": "claude-opus-4-7",
}


@dataclass
class ProductionResult:
    brief: EpisodeBrief
    clips: list[ClipCandidate]
    packages: list[ClipPackage]
    critic: CriticResult | None = None
    dropped_by_critic: list[int] = field(default_factory=list)  # clip indices dropped


ProgressCb = Callable[[float, str], None]


def _p(progress_cb: ProgressCb | None, pct: float, msg: str) -> None:
    if progress_cb:
        try:
            progress_cb(pct, msg)
        except Exception:
            pass


def produce_clips(
    transcript: Transcript,
    meta: SourceMeta | None = None,
    *,
    video_slug: str = "",
    taste_profile: str = "",
    max_clips: int = 10,
    min_score: int = 70,
    models: dict[str, str] | None = None,
    client: Anthropic | None = None,
    progress_cb: ProgressCb | None = None,
    run_critic_pass: bool = True,
) -> ProductionResult:
    """Full multi-agent clipping pipeline."""
    client = client or Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    m = {**MODELS, **(models or {})}

    # 1) Producer (brain)
    _p(progress_cb, 0.02, "Producer reading episode & writing brief…")
    brief = run_producer(
        transcript, meta,
        taste_profile=taste_profile,
        client=client, model=m["producer"],
    )
    target = min(max_clips, brief.target_clip_count or max_clips)
    _p(progress_cb, 0.15, f"Brief ready · domain: {brief.domain or 'unknown'} · {len(brief.archetypes)} archetypes")

    # 2) Scouts (parallel per-window)
    windows = transcript.windowed(window_sec=900.0, overlap_sec=60.0) or [transcript]
    _p(progress_cb, 0.18, f"Deploying {len(windows)} scout(s)…")

    def scout_one(window: Transcript) -> list[ClipCandidate]:
        return run_scout_on_window(
            window, transcript, brief,
            taste_profile=taste_profile,
            client=client, model=m["scout"],
            video_slug=video_slug,
        )

    candidates: list[ClipCandidate] = []
    done = 0
    with ThreadPoolExecutor(max_workers=min(4, len(windows))) as pool:
        futures = [pool.submit(scout_one, w) for w in windows]
        for fut in futures:
            cands = fut.result() or []
            candidates.extend(cands)
            done += 1
            _p(progress_cb, 0.2 + 0.35 * (done / len(windows)),
               f"Scout {done}/{len(windows)} found {len(cands)} candidate(s)")

    # Early filter: min score, dedupe by overlap
    candidates.sort(key=lambda c: c.score, reverse=True)
    deduped: list[ClipCandidate] = []
    for cand in candidates:
        if cand.score < min_score:
            continue
        overlap = False
        for kept in deduped:
            inter = max(0.0, min(cand.end, kept.end) - max(cand.start, kept.start))
            if inter / max(cand.duration, 1.0) > 0.5:
                overlap = True
                break
        if not overlap:
            deduped.append(cand)
    # Cap the set the Editor has to reason about.
    editor_input = deduped[: max(target * 2, 12)]
    _p(progress_cb, 0.58, f"Scouts surfaced {len(candidates)} → {len(editor_input)} pre-filtered")

    # 3) Editor: refine the finalist set
    _p(progress_cb, 0.6, "Editor refining boundaries and enforcing diversity…")
    refined = run_editor(
        editor_input, brief, transcript,
        client=client, model=m["editor"],
        target_count=target, video_slug=video_slug,
    )
    if not refined:
        refined = editor_input[:target]
    _p(progress_cb, 0.75, f"Editor kept {len(refined)} clip(s)")

    # 4) Critic (optional, but on by default)
    critic_result: CriticResult | None = None
    dropped: list[int] = []
    if run_critic_pass and refined:
        _p(progress_cb, 0.78, "Critic reviewing set for coverage + quality…")
        critic_result = run_critic(refined, brief, client=client, model=m["critic"])
        kept_clips: list[ClipCandidate] = []
        for i, clip in enumerate(refined, 1):
            verdict = next((d for d in critic_result.decisions if d.clip_index == i), None)
            if verdict and verdict.verdict == "drop":
                dropped.append(i)
                continue
            if verdict and verdict.verdict == "flag" and verdict.note:
                clip.rationale = (clip.rationale + f" [Critic flag: {verdict.note}]")[:400]
            kept_clips.append(clip)
        refined = kept_clips
        _p(progress_cb, 0.85, f"Critic dropped {len(dropped)} · kept {len(refined)}")

    # 5) Packager
    _p(progress_cb, 0.87, "Packager writing titles, captions, hashtags, thumbnails…")
    packages = run_packager(refined, brief, client=client, model=m["packager"]) if refined else []
    _p(progress_cb, 1.0, f"Done · {len(refined)} clip(s) packaged")

    return ProductionResult(
        brief=brief, clips=refined, packages=packages,
        critic=critic_result, dropped_by_critic=dropped,
    )
