"""Optional speaker diarization with pyannote.audio.

Activation: install pyannote.audio and set HUGGINGFACE_TOKEN to a token that
has accepted the pyannote/speaker-diarization-3.1 model license on HF.

If unavailable, `diarize_video()` returns None and callers should fall back
to unlabeled captions. The module is self-contained so the rest of the app
works without pyannote installed.

Output is a SpeakerTimeline mapping word-level transcripts to stable speaker
IDs (SPEAKER_00, SPEAKER_01, ...) plus a rename helper so the creator can
assign human-friendly names in the UI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .transcript import Segment, Transcript, Word


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str  # e.g. "SPEAKER_00"


@dataclass
class SpeakerTimeline:
    turns: list[SpeakerTurn] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)  # SPEAKER_00 -> "Alex", etc.

    def speaker_at(self, t: float) -> str | None:
        best: SpeakerTurn | None = None
        for turn in self.turns:
            if turn.start <= t < turn.end:
                return turn.speaker
            if turn.start > t:
                break
            best = turn
        return best.speaker if best else None

    def label(self, speaker: str) -> str:
        return self.labels.get(speaker, speaker.replace("SPEAKER_", "Speaker "))

    def to_dict(self) -> dict:
        return {
            "turns": [{"start": t.start, "end": t.end, "speaker": t.speaker} for t in self.turns],
            "labels": dict(self.labels),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SpeakerTimeline":
        tl = cls(labels=dict((d or {}).get("labels") or {}))
        for t in (d or {}).get("turns") or []:
            tl.turns.append(SpeakerTurn(start=float(t["start"]), end=float(t["end"]), speaker=str(t["speaker"])))
        return tl


def is_available() -> bool:
    try:
        import pyannote.audio  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN"))


def _extract_wav(video: Path, out_dir: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH — required for diarization audio extraction")
    out = out_dir / (video.stem + ".diarize.wav")
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        ffmpeg, "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-sample_fmt", "s16",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg wav extract failed: {proc.stderr[-500:]}")
    return out


def diarize_video(
    video_path: Path,
    *,
    out_dir: Path | None = None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> SpeakerTimeline | None:
    """Run pyannote diarization; return a SpeakerTimeline or None if unavailable."""
    if not is_available():
        return None
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        return None

    token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    work = Path(out_dir or tempfile.mkdtemp())
    wav = _extract_wav(Path(video_path), work)

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", use_auth_token=token,
    )
    kwargs: dict = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    diarization = pipeline(str(wav), **kwargs)
    timeline = SpeakerTimeline()
    for seg, _, spk in diarization.itertracks(yield_label=True):
        timeline.turns.append(SpeakerTurn(start=float(seg.start), end=float(seg.end), speaker=str(spk)))
    timeline.turns.sort(key=lambda t: t.start)
    return timeline


def attribute_words(words: Iterable[Word], timeline: SpeakerTimeline) -> list[tuple[Word, str]]:
    """Tag each word with the speaker at its midpoint."""
    out: list[tuple[Word, str]] = []
    for w in words:
        mid = (w.start + w.end) / 2.0
        spk = timeline.speaker_at(mid) or "SPEAKER_??"
        out.append((w, spk))
    return out
