"""Whisper-based transcription fallback.

Extracts low-bitrate mono audio with ffmpeg, splits into <24MB chunks, calls
OpenAI's Whisper API with verbose_json for segment + word timings, merges the
results with correct time offsets, and writes a Whisper-style JSON transcript
next to the source.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .cutter import require_ffmpeg

# Whisper API limit is 25 MB; leave headroom.
MAX_CHUNK_BYTES = 24 * 1024 * 1024
AUDIO_BITRATE_KBPS = 32           # mono speech, plenty for transcription
AUDIO_HZ = 16_000
SECONDS_PER_CHUNK = 10 * 60       # ~2.4 MB at 32 kbps


def _audio_duration(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-i", str(path)], capture_output=True, text=True
    )
    # ffmpeg writes duration to stderr: "Duration: 01:02:05.42, ..."
    for line in proc.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            ts = line.split(",", 1)[0].split("Duration:", 1)[1].strip()
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"Could not determine duration of {path}")


def _extract_audio(video: Path, out_dir: Path) -> Path:
    ffmpeg = require_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / (video.stem + ".whisper.mp3")
    if audio.exists() and audio.stat().st_size > 0:
        return audio
    cmd = [
        ffmpeg, "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(AUDIO_HZ),
        "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        str(audio),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extract failed: {proc.stderr[-500:]}")
    return audio


def _split_audio(audio: Path, chunk_dir: Path) -> list[tuple[Path, float]]:
    """Return [(chunk_path, start_offset_seconds), ...]."""
    ffmpeg = require_ffmpeg()
    if audio.stat().st_size <= MAX_CHUNK_BYTES:
        return [(audio, 0.0)]
    chunk_dir.mkdir(parents=True, exist_ok=True)
    duration = _audio_duration(ffmpeg, audio)
    chunks: list[tuple[Path, float]] = []
    t = 0.0
    i = 0
    while t < duration:
        out = chunk_dir / f"{audio.stem}.part{i:03d}.mp3"
        cmd = [
            ffmpeg, "-y", "-ss", f"{t:.3f}", "-i", str(audio),
            "-t", f"{SECONDS_PER_CHUNK}",
            "-c", "copy", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio split failed at t={t}: {proc.stderr[-500:]}")
        chunks.append((out, t))
        t += SECONDS_PER_CHUNK
        i += 1
    return chunks


def _transcribe_chunk(client, chunk: Path) -> dict:
    with chunk.open("rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )
    # openai>=1.0 returns a pydantic-ish object; dump to dict.
    return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)


def _merge_whisper(parts: list[tuple[dict, float]]) -> dict:
    merged_segments: list[dict] = []
    merged_words: list[dict] = []
    texts: list[str] = []
    for data, offset in parts:
        texts.append((data.get("text") or "").strip())
        for seg in data.get("segments") or []:
            ns = dict(seg)
            ns["start"] = float(seg.get("start", 0.0)) + offset
            ns["end"] = float(seg.get("end", 0.0)) + offset
            ns_words = []
            for w in (seg.get("words") or []):
                nw = dict(w)
                nw["start"] = float(w.get("start", 0.0)) + offset
                nw["end"] = float(w.get("end", 0.0)) + offset
                ns_words.append(nw)
            if ns_words:
                ns["words"] = ns_words
            merged_segments.append(ns)
        for w in data.get("words") or []:
            nw = dict(w)
            nw["start"] = float(w.get("start", 0.0)) + offset
            nw["end"] = float(w.get("end", 0.0)) + offset
            merged_words.append(nw)
    return {
        "text": " ".join(t for t in texts if t),
        "segments": merged_segments,
        "words": merged_words,
    }


@dataclass
class TranscribeOptions:
    model: str = "whisper-1"
    language: str | None = None


def transcribe_video(video: Path, out_dir: Path, opts: TranscribeOptions | None = None) -> Path:
    """Transcribe a video file. Returns the path to a Whisper JSON transcript."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "openai is required for auto-transcription. Install with: pip install openai"
        ) from e
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Either export it, or supply a transcript "
            "file next to the video to skip auto-transcription."
        )
    opts = opts or TranscribeOptions()
    out_dir = Path(out_dir)
    transcript_path = out_dir / (video.stem + ".json")
    if transcript_path.exists() and transcript_path.stat().st_size > 0:
        return transcript_path

    audio = _extract_audio(video, out_dir / ".audio")
    chunks = _split_audio(audio, out_dir / ".audio" / "chunks")
    client = OpenAI()
    parts: list[tuple[dict, float]] = []
    for chunk, offset in chunks:
        data = _transcribe_chunk(client, chunk)
        parts.append((data, offset))
    merged = _merge_whisper(parts)
    transcript_path.write_text(json.dumps(merged), encoding="utf-8")
    return transcript_path
