"""Transcript parsing for SRT, VTT, Whisper JSON, and plain text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    source_path: Path
    segments: list[Segment]

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    def windowed(self, window_sec: float = 900.0, overlap_sec: float = 60.0) -> list["Transcript"]:
        """Split into overlapping windows so long episodes fit into prompts."""
        if not self.segments:
            return []
        windows: list[Transcript] = []
        total = self.duration
        start = 0.0
        while start < total:
            end = min(start + window_sec, total)
            segs = [s for s in self.segments if s.end > start and s.start < end]
            if segs:
                windows.append(Transcript(source_path=self.source_path, segments=segs))
            if end >= total:
                break
            start = end - overlap_sec
        return windows


_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _parse_timestamp(s: str) -> float:
    m = _TS_RE.search(s)
    if not m:
        return 0.0
    h, mn, sec, ms = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(sec) + int(ms) / 1000.0


def _parse_srt_or_vtt(text: str, path: Path) -> Transcript:
    # Strip VTT header if present
    if text.lstrip().upper().startswith("WEBVTT"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    blocks = re.split(r"\n\s*\n", text.strip())
    segments: list[Segment] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # SRT has an index line; VTT usually doesn't. Find the timing line.
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_idx is None:
            continue
        timing = lines[timing_idx]
        left, _, right = timing.partition("-->")
        start = _parse_timestamp(left)
        end = _parse_timestamp(right)
        body = " ".join(lines[timing_idx + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)  # strip VTT tags
        if body:
            segments.append(Segment(start=start, end=end, text=body))
    return Transcript(source_path=path, segments=segments)


def _parse_whisper_json(data: dict, path: Path) -> Transcript:
    segments: list[Segment] = []
    raw_segments = data.get("segments") or []
    for seg in raw_segments:
        words = [
            Word(start=w.get("start", 0.0), end=w.get("end", 0.0), text=w.get("word") or w.get("text", ""))
            for w in (seg.get("words") or [])
        ]
        segments.append(
            Segment(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=(seg.get("text") or "").strip(),
                words=words,
            )
        )
    if not segments and "text" in data:
        segments.append(Segment(start=0.0, end=0.0, text=data["text"].strip()))
    return Transcript(source_path=path, segments=segments)


def _parse_plain_text(text: str, path: Path) -> Transcript:
    # Best-effort: one segment per paragraph with no timing info.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    segments = [Segment(start=0.0, end=0.0, text=p) for p in paragraphs]
    return Transcript(source_path=path, segments=segments)


def parse_transcript(path: Path) -> Transcript:
    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".srt", ".vtt"}:
        return _parse_srt_or_vtt(text, path)
    if suffix == ".json":
        return _parse_whisper_json(json.loads(text), path)
    return _parse_plain_text(text, path)


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"
