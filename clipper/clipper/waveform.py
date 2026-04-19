"""Compact audio waveform generator.

For the clip-editing scrubber we serialize a small peaks array (one int per
visual pixel, scaled 0..1000) computed once from the source video's audio.
Cached per (video_id, start, end) so re-opens are instant.

Strategy:
    1. ffmpeg extracts mono 16kHz PCM s16le over the clip range to stdout.
    2. We compute max-abs per bucket of samples, with ~800 buckets (enough for
       a smooth widescreen draw). Scale to 0..1000.
    3. Return as JSON for the frontend.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


DEFAULT_BUCKETS = 800
SAMPLE_RATE = 16_000


@dataclass
class Waveform:
    peaks: list[int]     # 0..1000 per bucket
    start: float
    end: float
    sample_rate: int = SAMPLE_RATE
    buckets: int = DEFAULT_BUCKETS

    def to_dict(self) -> dict:
        return {
            "peaks": self.peaks,
            "start": self.start,
            "end": self.end,
            "sample_rate": self.sample_rate,
            "buckets": self.buckets,
        }


def compute_waveform(
    video_path: Path,
    start: float,
    end: float,
    *,
    buckets: int = DEFAULT_BUCKETS,
) -> Waveform:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH — required for waveform generation")
    if end <= start:
        raise ValueError(f"end must be > start (got {start}..{end})")
    duration = end - start
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}",
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg waveform extract failed: {proc.stderr[-300:].decode('utf-8', errors='replace')}"
        )
    raw = proc.stdout
    if not raw:
        return Waveform(peaks=[0] * buckets, start=start, end=end)
    samples = np.frombuffer(raw, dtype=np.int16)
    if samples.size == 0:
        return Waveform(peaks=[0] * buckets, start=start, end=end)
    # Bucket by index and take max-abs so spikes remain visible.
    edges = np.linspace(0, samples.size, buckets + 1, dtype=np.int64)
    peaks: list[int] = []
    for i in range(buckets):
        seg = samples[edges[i]:edges[i + 1]]
        if seg.size == 0:
            peaks.append(0)
            continue
        peaks.append(int(np.max(np.abs(seg))))
    m = max(peaks) or 1
    peaks = [int(round(p / m * 1000)) for p in peaks]
    return Waveform(peaks=peaks, start=start, end=end, buckets=buckets)
