"""Agent roles and data models for the multi-agent clipping pipeline.

Hierarchy:
    Producer (brain)     — reads the full episode, writes an Episode Brief
    Coordinator          — runs the pipeline, enforces coverage/diversity
      ├── Scout(s)       — per-window candidate generation vs the brief
      ├── Editor         — refines boundaries and rationales on finalists
      ├── Packager       — per-clip titles, platform captions, thumbnails
      └── Critic         — final quality + representation gate

This module only contains the agent functions and shared data models. The
orchestrator that runs them lives in `produce.py`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from anthropic import Anthropic

from .ingester import SourceMeta
from .scorer import (
    ClipCandidate,
    _excerpt,
    _extract_json,
    _render_window,
    _snap_to_words,
)
from .transcript import Segment, Transcript, format_timestamp


# =============================================================================
# Data models
# =============================================================================


@dataclass
class ClipArchetype:
    name: str              # "Contrarian Thesis", "Actionable Framework", ...
    description: str       # what kind of moment this targets
    hook_pattern: str      # example hook phrasing
    why_it_works: str      # why this archetype performs for this audience

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EpisodeBrief:
    """The Producer's strategic read on the episode."""

    domain: str = ""                    # e.g. "venture capital / AI startups"
    expert_persona: str = ""           # producer persona adopted for this domain
    summary: str = ""                   # 2-4 sentence episode summary
    host_style: str = ""
    guests: list[str] = field(default_factory=list)
    key_themes: list[str] = field(default_factory=list)
    narrative_arc: str = ""
    brand_pov: str = ""                 # what this creator uniquely brings
    audience: str = ""
    archetypes: list[ClipArchetype] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)   # sponsor reads, etc.
    target_clip_count: int = 8
    raw_output: str = ""                # full producer response, for transparency
    model: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["archetypes"] = [a for a in d["archetypes"]]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EpisodeBrief":
        d = dict(d or {})
        archs = [ClipArchetype(**a) for a in d.get("archetypes") or []]
        d["archetypes"] = archs
        return cls(**d)

    def as_prompt_block(self) -> str:
        """Condensed brief formatted for injection into other agent prompts."""
        arch_lines = "\n".join(
            f"  • [{a.name}] {a.description} — hook pattern: {a.hook_pattern}"
            for a in self.archetypes
        ) or "  (no archetypes)"
        avoid_lines = "\n".join(f"  - {a}" for a in self.avoid) or "  (none)"
        guests = ", ".join(self.guests) if self.guests else "(solo)"
        themes = " · ".join(self.key_themes) if self.key_themes else "(none)"
        return (
            f"DOMAIN: {self.domain}\n"
            f"AUDIENCE: {self.audience}\n"
            f"HOST STYLE: {self.host_style}\n"
            f"GUESTS: {guests}\n"
            f"KEY THEMES: {themes}\n"
            f"NARRATIVE ARC: {self.narrative_arc}\n"
            f"BRAND POV: {self.brand_pov}\n"
            f"SUMMARY: {self.summary}\n"
            f"\nCLIP ARCHETYPES TO PURSUE:\n{arch_lines}\n"
            f"\nAVOID:\n{avoid_lines}\n"
            f"\nTARGET: up to {self.target_clip_count} final clips that collectively represent the episode."
        )


@dataclass
class ClipPackage:
    """Per-clip social packaging produced by the Packager agent."""

    clip_id: int | None = None
    archetype: str = ""
    titles: list[str] = field(default_factory=list)
    platform_captions: dict[str, str] = field(default_factory=dict)
    hashtags: list[str] = field(default_factory=list)
    description: str = ""
    thumbnail_moment_sec: float | None = None
    thumbnail_reason: str = ""
    why_it_works: str = ""
    audience_appeal: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClipPackage":
        return cls(**(d or {}))


# =============================================================================
# Shared helpers
# =============================================================================


def _anthropic(client: Anthropic | None = None) -> Anthropic:
    return client or Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _coalesce_text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def _condensed_transcript(transcript: Transcript, *, max_chars: int = 40_000) -> str:
    """A timestamp-annotated, size-capped rendering for the Producer.

    Long episodes need to fit into a single producer call. We chunk by segment
    and drop the middle when we must, keeping head + tail (where intros and
    payoffs usually live).
    """
    lines: list[str] = []
    total = 0
    for seg in transcript.segments:
        line = f"[{format_timestamp(seg.start)}] {seg.text.strip()}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    if total < max_chars or not transcript.segments:
        return "\n".join(lines)
    # If we ran out, still include the final ~10% so the producer sees the conclusion.
    tail_budget = max_chars // 4
    tail: list[str] = []
    tail_total = 0
    for seg in reversed(transcript.segments):
        line = f"[{format_timestamp(seg.start)}] {seg.text.strip()}"
        if tail_total + len(line) + 1 > tail_budget:
            break
        tail.append(line)
        tail_total += len(line) + 1
    return "\n".join(lines) + "\n\n...[middle truncated]...\n\n" + "\n".join(reversed(tail))


# =============================================================================
# Producer (brain)
# =============================================================================


PRODUCER_SYSTEM = """You are the Producer — the creative brain of a world-class podcast clipping operation. You are about to work on a single episode.

Before any clipping happens, you read the whole episode and write an EPISODE BRIEF that every downstream agent will use.

Your job in this step:
1. Identify the domain (e.g. "AI / startups / venture capital", "endurance sport physiology", "criminal justice reform") and take on the PERSONA of an elite producer in that space — the kind of person who has produced the #1 podcasts in this topic and knows exactly what lands with that audience.
2. Summarize the episode faithfully and crisply. Do not exaggerate.
3. Capture the host's voice, the guest(s) if any, the actual narrative arc, and the key themes.
4. Articulate the BRAND POV — what this creator uniquely brings that no one else could.
5. Identify WHO the target audience is for this episode.
6. Propose 3–6 clip ARCHETYPES specifically tailored to THIS episode (not generic). Each archetype should be a distinct angle on the content that would perform on short-form social. Examples of archetypes: "Contrarian Thesis", "Actionable Framework", "Unlikely Origin Story", "Moral Stakes", "Craft Rant", "Quotable Maxim", "Vulnerable Admission", "Behind-the-Scenes Reveal". Make the names match the actual content.
7. List what to AVOID clipping: sponsor reads, intros, outros, inside references that require prior episodes, pure setup without payoff, logistical tangents.
8. Propose a TARGET CLIP COUNT for the final selection — enough to represent the episode well, not so many that the collection gets diluted. Default 6–10.

Output STRICT JSON with this shape and nothing else (no markdown fences, no prose):
{
  "domain": "...",
  "expert_persona": "One sentence describing the producer persona you've adopted.",
  "summary": "2-4 faithful sentences.",
  "host_style": "...",
  "guests": ["..."],
  "key_themes": ["..."],
  "narrative_arc": "...",
  "brand_pov": "...",
  "audience": "...",
  "archetypes": [
    {"name": "...", "description": "...", "hook_pattern": "example opening line", "why_it_works": "why this resonates with THIS audience"}
  ],
  "avoid": ["..."],
  "target_clip_count": 8
}
Accuracy and specificity are paramount. Vague generalities are a failure."""


def run_producer(
    transcript: Transcript,
    meta: SourceMeta | None,
    *,
    taste_profile: str = "",
    client: Anthropic | None = None,
    model: str = "claude-opus-4-7",
) -> EpisodeBrief:
    client = _anthropic(client)
    meta = meta or SourceMeta()
    meta_block = (
        f"SOURCE METADATA (may be incomplete):\n"
        f"  title: {meta.title}\n"
        f"  channel: {meta.channel or meta.uploader}\n"
        f"  channel bio: {meta.channel_description[:800]}\n"
        f"  video description: {meta.video_description[:1200]}\n"
        f"  duration: {meta.duration_sec/60:.1f} min\n"
        f"  tags: {', '.join(meta.tags[:15])}\n"
        f"  view count: {meta.view_count}\n"
    ) if meta.title or meta.channel else ""

    taste_block = (
        f"EDITOR TASTE PROFILE (honor this on top of your general expertise):\n{taste_profile.strip()}\n\n"
        if taste_profile.strip() else ""
    )

    user_text = (
        f"{meta_block}\n"
        f"{taste_block}"
        f"FULL EPISODE TRANSCRIPT (timestamps in seconds since start):\n\n"
        f"{_condensed_transcript(transcript)}"
    )

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=[
            {"type": "text", "text": PRODUCER_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        # Non-fatal: return a stub brief so the pipeline still runs.
        return EpisodeBrief(raw_output=text, model=model)

    archs = [ClipArchetype(**a) for a in (data.get("archetypes") or []) if isinstance(a, dict)]
    return EpisodeBrief(
        domain=str(data.get("domain", "")).strip(),
        expert_persona=str(data.get("expert_persona", "")).strip(),
        summary=str(data.get("summary", "")).strip(),
        host_style=str(data.get("host_style", "")).strip(),
        guests=[str(g) for g in (data.get("guests") or [])][:6],
        key_themes=[str(t) for t in (data.get("key_themes") or [])][:8],
        narrative_arc=str(data.get("narrative_arc", "")).strip(),
        brand_pov=str(data.get("brand_pov", "")).strip(),
        audience=str(data.get("audience", "")).strip(),
        archetypes=archs[:6],
        avoid=[str(a) for a in (data.get("avoid") or [])][:10],
        target_clip_count=int(data.get("target_clip_count") or 8),
        raw_output=text,
        model=model,
    )


# =============================================================================
# Scout
# =============================================================================


SCOUT_SYSTEM = """You are a SCOUT agent working for the Producer. You have been assigned one window of the transcript. The Producer has given you an EPISODE BRIEF with the strategic angles that matter for this specific episode.

Your job: find moments in your window that are the BEST possible fits for the archetypes in the brief. Quality over quantity. Only surface moments you'd stake your reputation on — if nothing in this window meets the bar, return an empty list.

Each proposed clip must:
- Map to one archetype from the brief (return its name in "archetype")
- Start with a genuinely strong hook (first 3 seconds)
- Stand alone without requiring earlier context from the episode
- End on a real payoff, not mid-thought
- Honor AVOID items from the brief
- Be 25–120 seconds

For each clip, return:
- start, end (seconds as float)
- archetype (must match a brief archetype name exactly)
- title (<= 60 chars, punchy, feed-native)
- hook (verbatim first sentence of the clip)
- rationale (one sentence on why this lands for THIS audience)
- score (1-100, honest virality prediction for this creator's audience)
- tags (2-5 short topical tags)

Return STRICT JSON: {"clips": [...]}. No prose outside the JSON."""


def run_scout_on_window(
    transcript_window: Transcript,
    full_transcript: Transcript,
    brief: EpisodeBrief,
    *,
    taste_profile: str = "",
    client: Anthropic | None = None,
    model: str = "claude-sonnet-4-6",
    video_slug: str = "",
) -> list[ClipCandidate]:
    client = _anthropic(client)
    body = _render_window(transcript_window)

    system_blocks: list[dict] = [
        {"type": "text", "text": SCOUT_SYSTEM, "cache_control": {"type": "ephemeral"}},
        {
            "type": "text",
            "text": "EPISODE BRIEF:\n\n" + brief.as_prompt_block(),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if taste_profile.strip():
        system_blocks.append(
            {
                "type": "text",
                "text": "EDITOR TASTE PROFILE:\n\n" + taste_profile.strip(),
                "cache_control": {"type": "ephemeral"},
            }
        )

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system_blocks,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Transcript window for episode '{video_slug}'.\n"
                            f"Find up to 8 clip candidates aligned with the brief.\n\n"
                            f"{body}"
                        ),
                    }
                ],
            }
        ],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        return []

    out: list[ClipCandidate] = []
    for clip in data.get("clips", []) or []:
        try:
            start, end = _snap_to_words(float(clip["start"]), float(clip["end"]), transcript_window.segments)
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < 5:
            continue
        out.append(
            ClipCandidate(
                video_slug=video_slug,
                start=start,
                end=end,
                title=str(clip.get("title", "")).strip()[:120],
                hook=str(clip.get("hook", "")).strip()[:300],
                rationale=str(clip.get("rationale", "")).strip()[:400],
                score=int(clip.get("score", 0)),
                tags=[str(t)[:40] for t in (clip.get("tags") or [])][:6],
                transcript_excerpt=_excerpt(full_transcript.segments, start, end),
            )
        )
        # Stash the scout-assigned archetype on the tags for downstream use.
        arch = str(clip.get("archetype", "")).strip()
        if arch:
            out[-1].tags = [f"arch:{arch}"] + [t for t in out[-1].tags if not t.startswith("arch:")]
    return out


# =============================================================================
# Editor
# =============================================================================


EDITOR_SYSTEM = """You are the EDITOR. Scouts have surfaced candidate clips. Your job is to refine the finalist set so every clip is as tight and as strong as it can be.

For each candidate you're given, decide:
- KEEP: adjust start/end if it would improve the hook or payoff; rewrite the title to be punchier; sharpen the rationale; adjust score if warranted; tag it with one archetype from the brief.
- DROP: remove it if it doesn't meet the bar or overlaps a better clip in theme (not just timestamp).

Enforce diversity across archetypes — the final set should collectively represent the episode, not concentrate on a single theme.

For every KEPT clip, return:
- start, end (possibly adjusted)
- archetype (exact name from brief)
- title (refined)
- hook (verbatim first sentence of the final clip)
- rationale (one strong sentence)
- score (1-100, adjusted)
- tags (2-5)

Return STRICT JSON: {"clips": [...]}. Omit dropped ones. No prose outside the JSON."""


def run_editor(
    candidates: list[ClipCandidate],
    brief: EpisodeBrief,
    full_transcript: Transcript,
    *,
    client: Anthropic | None = None,
    model: str = "claude-opus-4-7",
    target_count: int | None = None,
    video_slug: str = "",
) -> list[ClipCandidate]:
    if not candidates:
        return []
    client = _anthropic(client)
    target = target_count or brief.target_clip_count or 8

    # Build a compact candidate dossier with snippets.
    dossier: list[str] = []
    for i, c in enumerate(candidates, 1):
        existing_arch = next((t.split(":", 1)[1] for t in c.tags if t.startswith("arch:")), "")
        dossier.append(
            f"[{i}] start={c.start:.1f}s end={c.end:.1f}s score={c.score} archetype={existing_arch}\n"
            f"    title: {c.title}\n"
            f"    hook:  {c.hook}\n"
            f"    why:   {c.rationale}\n"
            f"    tags:  {', '.join(t for t in c.tags if not t.startswith('arch:'))}\n"
            f"    text:  {c.transcript_excerpt[:500]}"
        )
    dossier_text = "\n\n".join(dossier)

    resp = client.messages.create(
        model=model,
        max_tokens=3000,
        system=[
            {"type": "text", "text": EDITOR_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": "EPISODE BRIEF:\n\n" + brief.as_prompt_block(),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Episode: '{video_slug}'. Produce a FINAL SET of up to {target} clips. "
                            f"Enforce diversity across archetypes.\n\n"
                            f"CANDIDATES:\n\n{dossier_text}"
                        ),
                    }
                ],
            }
        ],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        return candidates[:target]

    refined: list[ClipCandidate] = []
    for clip in data.get("clips", []) or []:
        try:
            start, end = _snap_to_words(
                float(clip["start"]), float(clip["end"]), full_transcript.segments
            )
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < 5:
            continue
        arch = str(clip.get("archetype", "")).strip()
        tags = [f"arch:{arch}"] if arch else []
        tags += [str(t)[:40] for t in (clip.get("tags") or [])][:5]
        refined.append(
            ClipCandidate(
                video_slug=video_slug,
                start=start,
                end=end,
                title=str(clip.get("title", "")).strip()[:120],
                hook=str(clip.get("hook", "")).strip()[:300],
                rationale=str(clip.get("rationale", "")).strip()[:400],
                score=int(clip.get("score", 0)),
                tags=tags,
                transcript_excerpt=_excerpt(full_transcript.segments, start, end),
            )
        )
    return refined[:target] or candidates[:target]


# =============================================================================
# Packager
# =============================================================================


PACKAGER_SYSTEM = """You are the PACKAGER. For each finalist clip, produce the complete social-ready package: titles, per-platform captions, hashtags, a thumbnail moment, and a short "why this works" note in the creator's voice.

Rules:
- Titles: 3 distinct variants. Short (<=60 chars), feed-native. At least one provocative/question form. Never clickbait that misrepresents the content.
- Captions: one per platform. Match each platform's native style:
  * tiktok: punchy opener + 1-2 line build, informal voice
  * reels: similar to tiktok, slightly more polished
  * shorts: 1-2 lines, SEO-aware
  * x: tweet-style with a strong line-break hook, 240 char max
  * linkedin: professional tone, offers an insight, 2-3 sentences
- Hashtags: 5-10, mixing broad reach and niche precision. Honor the episode domain.
- Description: 2-3 sentences including a quotable line from the clip.
- Thumbnail moment: a specific timestamp inside the clip that would make a strong thumbnail still, with a brief reason (what the viewer would see / feel).
- Why it works: one sentence in the creator's voice, grounded in the episode's audience.
- Audience appeal: one sentence on which segment of the audience this targets.

Return STRICT JSON: {"packages": [{"clip_index": <1-based>, "archetype": "...", "titles": [...], "platform_captions": {...}, "hashtags": [...], "description": "...", "thumbnail_moment_sec": <float>, "thumbnail_reason": "...", "why_it_works": "...", "audience_appeal": "..."}]}.
No prose outside the JSON."""


def run_packager(
    clips: list[ClipCandidate],
    brief: EpisodeBrief,
    *,
    client: Anthropic | None = None,
    model: str = "claude-sonnet-4-6",
) -> list[ClipPackage]:
    if not clips:
        return []
    client = _anthropic(client)
    dossier: list[str] = []
    for i, c in enumerate(clips, 1):
        arch = next((t.split(":", 1)[1] for t in c.tags if t.startswith("arch:")), "")
        dossier.append(
            f"[{i}] ({c.start:.1f}-{c.end:.1f}s, archetype={arch}, score={c.score})\n"
            f"    title: {c.title}\n"
            f"    hook: {c.hook}\n"
            f"    transcript: {c.transcript_excerpt[:700]}"
        )
    dossier_text = "\n\n".join(dossier)

    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=[
            {"type": "text", "text": PACKAGER_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": "EPISODE BRIEF:\n\n" + brief.as_prompt_block(),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Package ALL {len(clips)} clip(s) below. clip_index is 1-based and must map to the list.\n\n"
                            f"{dossier_text}"
                        ),
                    }
                ],
            }
        ],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        return [ClipPackage(archetype=next((t.split(':',1)[1] for t in c.tags if t.startswith('arch:')), '')) for c in clips]

    by_index: dict[int, ClipPackage] = {}
    for pkg in data.get("packages", []) or []:
        try:
            idx = int(pkg.get("clip_index"))
        except (TypeError, ValueError):
            continue
        thumb = pkg.get("thumbnail_moment_sec")
        try:
            thumb_f = float(thumb) if thumb is not None else None
        except (TypeError, ValueError):
            thumb_f = None
        captions = pkg.get("platform_captions") or {}
        if not isinstance(captions, dict):
            captions = {}
        by_index[idx] = ClipPackage(
            archetype=str(pkg.get("archetype", "")).strip(),
            titles=[str(t).strip()[:120] for t in (pkg.get("titles") or [])][:5],
            platform_captions={k: str(v)[:600] for k, v in captions.items()},
            hashtags=[str(h).strip().lstrip("#")[:40] for h in (pkg.get("hashtags") or [])][:12],
            description=str(pkg.get("description", "")).strip()[:600],
            thumbnail_moment_sec=thumb_f,
            thumbnail_reason=str(pkg.get("thumbnail_reason", "")).strip()[:300],
            why_it_works=str(pkg.get("why_it_works", "")).strip()[:300],
            audience_appeal=str(pkg.get("audience_appeal", "")).strip()[:300],
        )
    # Align by order — missing packages get empty placeholders.
    out: list[ClipPackage] = []
    for i, c in enumerate(clips, 1):
        pkg = by_index.get(i) or ClipPackage()
        if not pkg.archetype:
            pkg.archetype = next((t.split(":", 1)[1] for t in c.tags if t.startswith("arch:")), "")
        out.append(pkg)
    return out


# =============================================================================
# Critic
# =============================================================================


FAITHFULNESS_SYSTEM = """You are the FAITHFULNESS reviewer. Your only job is to protect the creator's reputation by catching clips that could misrepresent them or their guest when shown standalone on social media.

For each clip you see:
1. Its transcript text.
2. The surrounding ~60 seconds of episode context (BEFORE and AFTER the clip boundary).

You will decide a faithfulness verdict:
- "safe"      — the clip, played alone, faithfully represents what was said and meant.
- "risky"     — the clip is technically accurate but could be misread by a social audience (e.g. rhetorical example taken as literal claim, sarcasm mistaken for sincerity, incomplete thought).
- "unsafe"    — the clip would misrepresent the speaker (e.g. devil's-advocate quote stated as belief, hypothetical as fact, out-of-context accusation).

For every clip, return:
- clip_index (1-based)
- verdict (safe | risky | unsafe)
- concern (one sentence, empty string if safe)
- fix_hint (one sentence of how to adjust boundaries or add a caption disclaimer, if risky/unsafe; empty otherwise)

Bias toward "safe" — only flag genuine misrepresentation risk, not stylistic choices. Return STRICT JSON:
{"decisions": [{"clip_index": 1, "verdict": "safe", "concern": "", "fix_hint": ""}, ...]}
No prose outside the JSON."""


@dataclass
class FaithfulnessDecision:
    clip_index: int
    verdict: str         # safe | risky | unsafe
    concern: str
    fix_hint: str


@dataclass
class FaithfulnessResult:
    decisions: list[FaithfulnessDecision]


def _context_excerpt(full_transcript: Transcript, start: float, end: float, *, window: float = 60.0) -> tuple[str, str, str]:
    """Get the (before, during, after) transcript text around a clip range."""
    before: list[str] = []
    during: list[str] = []
    after: list[str] = []
    for seg in full_transcript.segments:
        if seg.end < start - window:
            continue
        if seg.start > end + window:
            break
        txt = seg.text.strip()
        if not txt:
            continue
        if seg.end < start:
            before.append(txt)
        elif seg.start > end:
            after.append(txt)
        else:
            during.append(txt)
    return " ".join(before)[-800:], " ".join(during)[:1200], " ".join(after)[:800]


def run_faithfulness(
    clips: list[ClipCandidate],
    full_transcript: Transcript,
    *,
    client: Anthropic | None = None,
    model: str = "claude-opus-4-7",
) -> FaithfulnessResult:
    if not clips:
        return FaithfulnessResult(decisions=[])
    client = _anthropic(client)
    dossier: list[str] = []
    for i, c in enumerate(clips, 1):
        before, during, after = _context_excerpt(full_transcript, c.start, c.end)
        dossier.append(
            f"[{i}] {c.start:.1f}-{c.end:.1f}s\n"
            f"    title: {c.title}\n"
            f"    BEFORE: {before}\n"
            f"    CLIP:   {during}\n"
            f"    AFTER:  {after}"
        )
    body = "\n\n".join(dossier)
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=[
            {"type": "text", "text": FAITHFULNESS_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": [{"type": "text", "text": body}]}],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        return FaithfulnessResult(
            decisions=[FaithfulnessDecision(i, "safe", "", "") for i in range(1, len(clips) + 1)]
        )
    decisions: list[FaithfulnessDecision] = []
    for d in data.get("decisions", []) or []:
        try:
            idx = int(d.get("clip_index"))
        except (TypeError, ValueError):
            continue
        verdict = str(d.get("verdict", "safe")).lower().strip()
        if verdict not in ("safe", "risky", "unsafe"):
            verdict = "safe"
        decisions.append(FaithfulnessDecision(
            clip_index=idx, verdict=verdict,
            concern=str(d.get("concern", "")).strip()[:300],
            fix_hint=str(d.get("fix_hint", "")).strip()[:300],
        ))
    return FaithfulnessResult(decisions=decisions)


CRITIC_SYSTEM = """You are the CRITIC. You are the last gate before these clips reach the creator for approval. You see the brief and the full final set. Your job is to ensure this set:
1. Accurately and fairly represents the episode — not a cherry-picked distortion.
2. Delivers on the BRAND POV.
3. Covers diverse archetypes (no heavy duplication).
4. Hits a high quality bar on hook strength and payoff.

For each clip, decide: KEEP, DROP, or FLAG. If FLAG, explain in one sentence what a human reviewer should consider. If you DROP, say briefly why.

Return STRICT JSON:
{"decisions": [{"clip_index": <1-based>, "verdict": "keep"|"drop"|"flag", "note": "..."}],
 "coverage_note": "one sentence on whether the set represents the episode well"}"""


@dataclass
class CriticDecision:
    clip_index: int
    verdict: str         # keep | drop | flag
    note: str


@dataclass
class CriticResult:
    decisions: list[CriticDecision]
    coverage_note: str


def run_critic(
    clips: list[ClipCandidate],
    brief: EpisodeBrief,
    *,
    client: Anthropic | None = None,
    model: str = "claude-opus-4-7",
) -> CriticResult:
    if not clips:
        return CriticResult(decisions=[], coverage_note="No clips to review.")
    client = _anthropic(client)
    dossier = "\n".join(
        f"[{i}] ({c.start:.1f}-{c.end:.1f}s, score={c.score}) {c.title}\n    hook: {c.hook}\n    why:  {c.rationale}"
        for i, c in enumerate(clips, 1)
    )
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=[
            {"type": "text", "text": CRITIC_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": "EPISODE BRIEF:\n\n" + brief.as_prompt_block(),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": [{"type": "text", "text": f"FINAL SET:\n\n{dossier}"}]}],
    )
    text = _coalesce_text(resp)
    try:
        data = _extract_json(text)
    except ValueError:
        return CriticResult(
            decisions=[CriticDecision(i, "keep", "") for i in range(1, len(clips) + 1)],
            coverage_note="",
        )
    decisions: list[CriticDecision] = []
    for d in data.get("decisions", []) or []:
        try:
            idx = int(d.get("clip_index"))
        except (TypeError, ValueError):
            continue
        verdict = str(d.get("verdict", "keep")).lower().strip()
        if verdict not in ("keep", "drop", "flag"):
            verdict = "keep"
        decisions.append(
            CriticDecision(clip_index=idx, verdict=verdict, note=str(d.get("note", "")).strip()[:400])
        )
    return CriticResult(decisions=decisions, coverage_note=str(data.get("coverage_note", "")).strip()[:400])
