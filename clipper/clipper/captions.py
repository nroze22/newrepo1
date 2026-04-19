"""Animated word-level captions via the ASS (Advanced SubStation Alpha) format.

ASS is the industry-standard styled-subtitle format used by DaVinci Resolve,
Premiere, Aegisub, and VLC. ffmpeg's `subtitles=` filter burns it in at full
quality — no rasterization artifacts, no per-frame overlay math.

We generate phrase-level events (2-5 words at a time) with karaoke-style
word highlights (\\k/\\kf), plus bouncy scale-in overrides (\\t + \\fscx/\\fscy)
for the "TikTok pop" styles. This gives word-by-word animated captions that
render natively on any ffmpeg build with libass.

Styles bundled:
    tiktok_pop      — bold white, accent-colored active word, scale pop
    clean_minimal   — thin white, subtle keyline, no animation
    hype_shadow     — heavy shadow, all caps, yellow active word
    news_ticker     — bottom-bar style, constant case

A BrandKit overrides the visual tokens (primary color, accent color, font).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .transcript import Segment, Transcript, Word


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class BrandKit:
    """Creator-scoped brand defaults. Any field can be overridden per-render."""

    font: str = "Inter"              # must be installed on the render host; ffmpeg falls back to sans
    primary_color: str = "#FFFFFF"   # main caption color
    accent_color: str = "#FFD84D"    # active word highlight
    shadow_color: str = "#000000"
    outline_px: int = 3
    shadow_px: int = 2
    uppercase: bool = False
    profanity_safe: bool = False
    logo_path: str | None = None     # optional PNG overlay (watermark)
    logo_opacity: float = 0.85
    logo_position: str = "top_right" # top_left | top_right | bottom_left | bottom_right


@dataclass
class CaptionStyle:
    key: str
    name: str
    font_size: int
    bold: bool
    uppercase: bool
    outline_px: int
    shadow_px: int
    pop_animation: bool          # scale-in per phrase
    active_word_highlight: bool  # karaoke-style active word color
    vertical_anchor: float       # 0..1 vertical position for the caption baseline

    @classmethod
    def preset(cls, key: str) -> "CaptionStyle":
        presets = {
            "tiktok_pop": cls(
                key="tiktok_pop", name="TikTok Pop",
                font_size=72, bold=True, uppercase=False,
                outline_px=4, shadow_px=3,
                pop_animation=True, active_word_highlight=True,
                vertical_anchor=0.78,
            ),
            "clean_minimal": cls(
                key="clean_minimal", name="Clean Minimal",
                font_size=56, bold=False, uppercase=False,
                outline_px=2, shadow_px=1,
                pop_animation=False, active_word_highlight=False,
                vertical_anchor=0.85,
            ),
            "hype_shadow": cls(
                key="hype_shadow", name="Hype Shadow",
                font_size=78, bold=True, uppercase=True,
                outline_px=3, shadow_px=6,
                pop_animation=True, active_word_highlight=True,
                vertical_anchor=0.72,
            ),
            "news_ticker": cls(
                key="news_ticker", name="News Ticker",
                font_size=46, bold=True, uppercase=False,
                outline_px=0, shadow_px=0,
                pop_animation=False, active_word_highlight=False,
                vertical_anchor=0.92,
            ),
        }
        if key not in presets:
            raise KeyError(f"Unknown caption style: {key!r}. Choose from {list(presets)}")
        return presets[key]


PROFANITY = {
    "fuck", "fucking", "fucked", "shit", "shitty", "bitch", "cunt", "dick",
    "asshole", "pussy", "bastard", "damn", "goddamn",
}


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------


def _hex_to_bgr_ass(hex_color: str) -> str:
    """Convert #RRGGBB to ASS BGR hex string (&HBBGGRR)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ass_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _censor(word: str) -> str:
    stripped = re.sub(r"[^A-Za-z']", "", word).lower()
    if stripped in PROFANITY:
        if len(word) <= 2:
            return "*" * len(word)
        return word[0] + "*" * (len(word) - 2) + word[-1]
    return word


# ---------------------------------------------------------------------------
# Phrase packing
# ---------------------------------------------------------------------------


@dataclass
class Phrase:
    start: float
    end: float
    words: list[Word]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def pack_phrases(
    words: Sequence[Word],
    *,
    max_words: int = 4,
    max_chars: int = 36,
    max_gap: float = 0.6,
) -> list[Phrase]:
    """Greedy pack words into caption-sized phrases, respecting natural pauses."""
    phrases: list[Phrase] = []
    buf: list[Word] = []
    for w in words:
        text = w.text.strip()
        if not text:
            continue
        if buf:
            gap = w.start - buf[-1].end
            joined_len = sum(len(b.text) + 1 for b in buf) + len(text)
            if (
                len(buf) >= max_words
                or joined_len > max_chars
                or gap > max_gap
                or buf[-1].text.rstrip().endswith((".", "?", "!"))
            ):
                phrases.append(Phrase(start=buf[0].start, end=buf[-1].end, words=buf))
                buf = []
        buf.append(w)
    if buf:
        phrases.append(Phrase(start=buf[0].start, end=buf[-1].end, words=buf))
    return phrases


def _collect_words(transcript: Transcript, start: float, end: float) -> list[Word]:
    """All words in [start, end) from a transcript, respecting its word timing if present."""
    out: list[Word] = []
    for seg in transcript.segments:
        if seg.end < start or seg.start > end:
            continue
        if seg.words:
            for w in seg.words:
                if w.end > start and w.start < end:
                    out.append(Word(start=w.start, end=w.end, text=w.text.strip()))
        else:
            # Fall back: spread the segment's text evenly over its duration.
            toks = [t for t in re.split(r"\s+", seg.text.strip()) if t]
            if not toks:
                continue
            dur = max(seg.end - seg.start, 0.01)
            step = dur / len(toks)
            for i, tok in enumerate(toks):
                ws = max(seg.start + i * step, start)
                we = min(ws + step, end)
                if we > ws:
                    out.append(Word(start=ws, end=we, text=tok))
    out.sort(key=lambda w: w.start)
    return out


# ---------------------------------------------------------------------------
# ASS rendering
# ---------------------------------------------------------------------------


def _style_header(style: CaptionStyle, kit: BrandKit, *, play_w: int, play_h: int) -> str:
    primary = _hex_to_bgr_ass(kit.primary_color)
    secondary = _hex_to_bgr_ass(kit.accent_color)
    shadow = _hex_to_bgr_ass(kit.shadow_color)
    weight = -1 if style.bold else 0
    margin_v = max(60, int(play_h * (1 - style.vertical_anchor)))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_w}\n"
        f"PlayResY: {play_h}\n"
        "ScaledBorderAndShadow: yes\n"
        "Collisions: Normal\n"
        "WrapStyle: 2\n"  # no automatic line wrap — we control line breaks
        "\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Cap,{kit.font},{style.font_size},{primary},{secondary},{shadow},{shadow},"
        f"{weight},0,0,0,100,100,0,0,1,{kit.outline_px or style.outline_px},"
        f"{kit.shadow_px or style.shadow_px},2,80,80,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )


def _phrase_dialogue(
    phrase: Phrase,
    clip_start: float,
    style: CaptionStyle,
    kit: BrandKit,
) -> str:
    # Time relative to clip start (ASS files are played over the clip).
    ps = max(0.0, phrase.start - clip_start)
    pe = max(ps + 0.05, phrase.end - clip_start)

    text_parts: list[str] = []
    if style.pop_animation:
        # Scale from 85% → 100% over 120ms and slight fade-in.
        text_parts.append(r"{\fad(80,0)\fscx85\fscy85\t(0,120,\fscx100\fscy100)}")

    for i, w in enumerate(phrase.words):
        token = w.text.strip()
        if not token:
            continue
        if style.uppercase:
            token = token.upper()
        if kit.profanity_safe:
            token = _censor(token)
        token = token.replace("{", "(").replace("}", ")")

        if style.active_word_highlight and len(phrase.words) > 1:
            # Duration per word, in centiseconds (\k/\kf precision).
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            # \kf does a smooth fill sweep across the word — looks great in tests.
            text_parts.append(rf"{{\kf{dur_cs}}}{token}")
        else:
            text_parts.append(token)
        if i < len(phrase.words) - 1:
            text_parts.append(" ")

    text = "".join(text_parts)
    return f"Dialogue: 0,{_ass_time(ps)},{_ass_time(pe)},Cap,,0,0,0,,{text}\n"


def build_ass(
    transcript: Transcript,
    *,
    clip_start: float,
    clip_end: float,
    style: CaptionStyle | str = "tiktok_pop",
    brand_kit: BrandKit | None = None,
    play_w: int = 1080,
    play_h: int = 1920,
) -> str:
    """Produce the full ASS document for one clip."""
    if isinstance(style, str):
        style = CaptionStyle.preset(style)
    brand_kit = brand_kit or BrandKit()

    words = _collect_words(transcript, clip_start, clip_end)
    phrases = pack_phrases(words)
    header = _style_header(style, brand_kit, play_w=play_w, play_h=play_h)
    events = "".join(_phrase_dialogue(p, clip_start, style, brand_kit) for p in phrases)
    return header + events


def write_ass(
    transcript: Transcript,
    out_path: Path,
    *,
    clip_start: float,
    clip_end: float,
    style: CaptionStyle | str = "tiktok_pop",
    brand_kit: BrandKit | None = None,
    play_w: int = 1080,
    play_h: int = 1920,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass = build_ass(
        transcript,
        clip_start=clip_start,
        clip_end=clip_end,
        style=style,
        brand_kit=brand_kit,
        play_w=play_w,
        play_h=play_h,
    )
    out_path.write_text(ass, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# SRT fallback (for export bundles)
# ---------------------------------------------------------------------------


def _srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec - h * 3600 - m * 60
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{int(s):02d},{ms:03d}"


def write_srt(
    transcript: Transcript,
    out_path: Path,
    *,
    clip_start: float,
    clip_end: float,
) -> Path:
    words = _collect_words(transcript, clip_start, clip_end)
    phrases = pack_phrases(words)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, p in enumerate(phrases, 1):
        s = max(0.0, p.start - clip_start)
        e = max(s + 0.05, p.end - clip_start)
        text = " ".join(w.text.strip() for w in p.words if w.text.strip())
        lines.append(f"{i}\n{_srt_time(s)} --> {_srt_time(e)}\n{text}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
