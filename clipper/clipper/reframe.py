"""Smart 9:16 reframe driven by MediaPipe face tracking.

Sampling strategy:
    - Open the source with OpenCV at the clip's time window.
    - Sample ~5 fps through the window (cheap enough for long clips).
    - Run MediaPipe Face Detection on each sample.
    - Build an x-center timeline of the most-confident face per sample,
      falling back to the previous known center on miss frames.
    - Clamp + low-pass filter to remove jitter.
    - Emit an ffmpeg `sendcmd` file so ffmpeg's `crop` filter pans smoothly
      over time during the actual cut.

Output of `plan_reframe()`:
    ReframePlan(
        source_width, source_height,
        sendcmd_path,    # ffmpeg sendcmd script
        target_w, target_h,
        notes,           # confidence, miss count, shake stats
    )

The cutter module consumes the plan and builds a filter graph of the form:
    sendcmd=f=<cmds>,
    crop=w=iw*9/16:h=ih:x=<initial>:y=0,
    scale=<target>:force_original_aspect_ratio=decrease,
    pad=<target>
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .vision import detect_best_face


@dataclass
class ReframePlan:
    source_width: int
    source_height: int
    target_w: int
    target_h: int
    sendcmd_path: Path | None
    initial_x: float           # starting crop X (pixels)
    miss_ratio: float          # 0..1 — fraction of sampled frames with no face
    shake_px: float            # median per-second jump magnitude (low = stable)
    faces_found: int
    frames_sampled: int
    timeline: list[tuple[float, float]]  # [(time_sec_within_clip, x_pixel), ...]

    @property
    def confident(self) -> bool:
        return self.faces_found >= 3 and self.miss_ratio < 0.5


def _sample_frames(
    video_path: Path,
    start: float,
    end: float,
    fps: float = 5.0,
) -> list[tuple[float, np.ndarray]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {video_path}")
    try:
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(native_fps / fps)))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        end_msec = end * 1000.0
        out: list[tuple[float, np.ndarray]] = []
        while True:
            pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if pos >= end:
                break
            ok, frame = cap.read()
            if not ok:
                break
            out.append((pos, frame))
            # Skip ahead by `step` frames to approximate the sampling fps.
            for _ in range(step - 1):
                if not cap.grab():
                    break
            if cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 >= end:
                break
            if cap.get(cv2.CAP_PROP_POS_MSEC) * 1000.0 > end_msec * 1000.0:
                break
        return out
    finally:
        cap.release()


def _detect_center(frame: np.ndarray) -> tuple[float | None, float]:
    """Return (x_center_px, confidence) for the most confident face in the frame, or (None, 0)."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    hit = detect_best_face(rgb)
    if hit is None:
        return None, 0.0
    return hit.cx, hit.confidence


def _smooth(timeline: list[tuple[float, float]], window_sec: float = 1.5) -> list[tuple[float, float]]:
    """Moving-average on the x-timeline to remove micro-jitter."""
    if not timeline:
        return timeline
    out: list[tuple[float, float]] = []
    for i, (t, x) in enumerate(timeline):
        lo = i
        hi = i
        while lo > 0 and t - timeline[lo - 1][0] <= window_sec:
            lo -= 1
        while hi < len(timeline) - 1 and timeline[hi + 1][0] - t <= window_sec:
            hi += 1
        xs = [tl[1] for tl in timeline[lo:hi + 1]]
        out.append((t, float(sum(xs) / len(xs))))
    return out


def _write_sendcmd(
    timeline: list[tuple[float, float]],
    crop_w: int,
    source_w: int,
    out_path: Path,
) -> None:
    """Emit an ffmpeg sendcmd script that drives the `crop` filter's x parameter over time."""
    lines = []
    max_x = max(0, source_w - crop_w)
    for t, x in timeline:
        x_clamped = max(0.0, min(float(x) - crop_w / 2.0, float(max_x)))
        # sendcmd syntax: "<time> <filter_label> <option> <value>;"
        lines.append(f"{t:.3f} crop x {x_clamped:.1f};")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def plan_reframe(
    video_path: Path,
    *,
    clip_start: float,
    clip_end: float,
    target_w: int = 1080,
    target_h: int = 1920,
    sendcmd_out: Path | None = None,
    sample_fps: float = 5.0,
) -> ReframePlan:
    """Analyze the clip and return a reframe plan usable by the cutter."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {video_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not src_w or not src_h:
        raise RuntimeError(f"Invalid video dimensions for {video_path}")

    # The crop window is the full source height (for landscape podcasts) with
    # a width that matches target aspect. For vertical sources we already have
    # 9:16 so there's nothing to do.
    aspect_target = target_w / target_h
    aspect_src = src_w / src_h
    if aspect_src <= aspect_target + 0.01:
        # Source is already as narrow as target — no x-panning needed.
        return ReframePlan(
            source_width=src_w, source_height=src_h,
            target_w=target_w, target_h=target_h,
            sendcmd_path=None,
            initial_x=0.0, miss_ratio=0.0, shake_px=0.0,
            faces_found=0, frames_sampled=0,
            timeline=[],
        )
    crop_w = int(round(src_h * aspect_target))

    samples = _sample_frames(video_path, clip_start, clip_end, fps=sample_fps)
    raw: list[tuple[float, float | None]] = []
    faces_found = 0
    for abs_t, frame in samples:
        cx, conf = _detect_center(frame)
        rel = abs_t - clip_start
        if cx is not None:
            raw.append((rel, cx))
            faces_found += 1
        else:
            raw.append((rel, None))

    # Fill misses by carrying the last known center, falling back to frame centerline.
    last = src_w / 2.0
    filled: list[tuple[float, float]] = []
    for rel, cx in raw:
        if cx is not None:
            last = cx
        filled.append((rel, last))
    smoothed = _smooth(filled, window_sec=1.5)

    # Shake metric: median per-step jump
    jumps = [abs(smoothed[i][1] - smoothed[i - 1][1]) for i in range(1, len(smoothed))]
    shake = float(statistics.median(jumps)) if jumps else 0.0

    # Prefix + suffix the timeline so ffmpeg's sendcmd has authoritative values
    # at t=0 and t=clip_duration even if we lost samples at boundaries.
    if smoothed:
        first_t, first_x = smoothed[0]
        last_t, last_x = smoothed[-1]
        if first_t > 0.05:
            smoothed.insert(0, (0.0, first_x))
        dur = clip_end - clip_start
        if last_t < dur - 0.05:
            smoothed.append((dur, last_x))

    initial_x = 0.0
    if smoothed:
        initial_x = max(0.0, min(smoothed[0][1] - crop_w / 2.0, float(src_w - crop_w)))

    if sendcmd_out and smoothed:
        _write_sendcmd(smoothed, crop_w, src_w, sendcmd_out)

    return ReframePlan(
        source_width=src_w, source_height=src_h,
        target_w=target_w, target_h=target_h,
        sendcmd_path=sendcmd_out if (sendcmd_out and smoothed) else None,
        initial_x=initial_x,
        miss_ratio=(1.0 - (faces_found / len(raw))) if raw else 0.0,
        shake_px=shake,
        faces_found=faces_found,
        frames_sampled=len(raw),
        timeline=smoothed,
    )


def build_reframe_filter_fragments(plan: ReframePlan) -> list[str]:
    """Return the ordered ffmpeg -vf fragments to reframe to plan.target_w x plan.target_h.

    The fragments are meant to be joined with commas and prepended before any
    caption-burn stage.
    """
    # If aspect already matches, just scale+pad — no crop panning.
    if plan.source_width / plan.source_height <= plan.target_w / plan.target_h + 0.01:
        return [
            f"scale={plan.target_w}:{plan.target_h}:force_original_aspect_ratio=decrease",
            f"pad={plan.target_w}:{plan.target_h}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]
    crop_w = int(round(plan.source_height * plan.target_w / plan.target_h))
    fragments: list[str] = []
    if plan.sendcmd_path:
        # Escape path for ffmpeg filter syntax.
        cmd_path = str(plan.sendcmd_path).replace("\\", "\\\\").replace(":", r"\:")
        fragments.append(f"sendcmd=f='{cmd_path}'")
    fragments.append(f"crop=w={crop_w}:h=ih:x={plan.initial_x:.1f}:y=0")
    fragments.append(f"scale={plan.target_w}:{plan.target_h}")
    return fragments
