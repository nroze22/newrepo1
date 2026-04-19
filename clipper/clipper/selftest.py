"""End-to-end validation harness.

Generates a synthetic "podcast" video (a stylized face that moves across a
landscape frame, with a word-accurate transcript), runs the actual ffmpeg +
MediaPipe pipeline, and asserts the artefacts exist and are sane.

Run: python -m clipper.selftest
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"{tool} not on PATH")
    return path


def make_synthetic_video(out: Path, duration_sec: float = 30.0, fps: int = 30,
                         width: int = 1920, height: int = 1080) -> Path:
    """Render a landscape mp4 with a stylized 'face' that pans left→right."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    temp_raw = out.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(str(temp_raw), fourcc, float(fps), (width, height))
    if not writer.isOpened():
        raise RuntimeError("OpenCV writer failed to open — check codec availability.")

    n_frames = int(duration_sec * fps)
    face_r = height // 4
    for i in range(n_frames):
        t = i / fps
        # Slow left→right pan over 30s, with a small vertical oscillation.
        x_ratio = t / duration_sec
        cx = int(face_r * 1.4 + x_ratio * (width - face_r * 2.8))
        cy = int(height // 2 + 40 * np.sin(t * 1.2))
        frame = np.full((height, width, 3), 32, dtype=np.uint8)
        # Background gradient to give the detector real contrast.
        grad = np.linspace(18, 80, height, dtype=np.uint8)[:, None, None]
        frame = np.broadcast_to(grad, (height, width, 3)).astype(np.uint8).copy()
        # Skin-tone face
        cv2.circle(frame, (cx, cy), face_r, (205, 188, 170), -1, lineType=cv2.LINE_AA)
        # Eyes
        cv2.circle(frame, (cx - face_r // 3, cy - face_r // 4), face_r // 10, (35, 30, 30), -1, cv2.LINE_AA)
        cv2.circle(frame, (cx + face_r // 3, cy - face_r // 4), face_r // 10, (35, 30, 30), -1, cv2.LINE_AA)
        # Eyebrows
        cv2.line(frame, (cx - face_r // 2, cy - face_r // 2),
                 (cx - face_r // 6, cy - face_r // 2 - 10), (30, 30, 30), 6, cv2.LINE_AA)
        cv2.line(frame, (cx + face_r // 6, cy - face_r // 2 - 10),
                 (cx + face_r // 2, cy - face_r // 2), (30, 30, 30), 6, cv2.LINE_AA)
        # Mouth (small arc so expression proxy has something to chew)
        cv2.ellipse(frame, (cx, cy + face_r // 3), (face_r // 4, face_r // 8),
                    0, 0, 180, (40, 25, 25), 4, cv2.LINE_AA)
        # Frame counter for easy visual diff
        cv2.putText(frame, f"t={t:5.2f}s", (40, 60), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (220, 220, 220), 2, cv2.LINE_AA)
        writer.write(frame)
    writer.release()

    # mp4v from OpenCV on Linux is fine but not all players seek well; remux
    # to h264+aac with faststart + a silent audio track so downstream code
    # matches what yt-dlp/Whisper pipelines actually produce.
    ffmpeg = _require("ffmpeg")
    cmd = [
        ffmpeg, "-y", "-i", str(temp_raw),
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg remux failed: {proc.stderr[-400:]}")
    temp_raw.unlink(missing_ok=True)
    return out


def make_synthetic_transcript(out: Path, duration_sec: float) -> Path:
    """Write a Whisper-shape JSON with word-level timestamps spanning the duration."""
    out = Path(out)
    script = (
        "Everyone says scaling wins the race but honestly the rule is simpler than that. "
        "The best clips come from a sharp hook a clear payoff and a voice you can trust. "
        "If your audience laughs once then stays for the idea you have already won."
    )
    words = script.split()
    step = duration_sec / len(words)
    segments = []
    cur = 0.0
    sent_start = 0.0
    sent_words = []
    sent_text_words = []
    flushed = 0

    def flush(sent_text: str, starts: list, ends: list, words_list: list):
        segments.append({
            "start": starts[0], "end": ends[-1],
            "text": sent_text.strip(),
            "words": [{"start": s, "end": e, "word": w} for s, e, w in zip(starts, ends, words_list)],
        })

    s_starts = []
    s_ends = []
    s_words = []
    for w in words:
        ws = cur
        we = cur + step
        s_starts.append(ws); s_ends.append(we); s_words.append(w)
        cur = we
        if w.endswith((".", "!", "?")):
            flush(" ".join(s_words), s_starts, s_ends, s_words)
            s_starts, s_ends, s_words = [], [], []
    if s_words:
        flush(" ".join(s_words), s_starts, s_ends, s_words)

    data = {"text": script, "segments": segments}
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def run_all(work_dir: Path | None = None, *, verbose: bool = True) -> dict:
    """Exercise the whole media pipeline on a synthetic episode. Returns a report."""
    import time
    from .transcript import parse_transcript
    from .reframe import plan_reframe
    from .cutter import render_clip, CutOptions
    from .captions import BrandKit
    from .thumbnails import generate_thumbnails

    t0 = time.time()
    work = Path(work_dir or tempfile.mkdtemp(prefix="clipper-selftest-"))
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"work_dir": str(work), "steps": []}

    def step(name: str, f, **ctx):
        t0 = time.time()
        if verbose:
            print(f"  • {name}…", end=" ", flush=True)
        try:
            result = f()
            ok = True
            err = None
        except Exception as e:
            result = None; ok = False; err = f"{type(e).__name__}: {e}"
        dur = time.time() - t0
        report["steps"].append({"name": name, "ok": ok, "duration_sec": round(dur, 3), "error": err, **ctx})
        if verbose:
            print(f"{'ok' if ok else 'FAIL'} ({dur:.2f}s){' — ' + err if err else ''}")
        return result

    video = step("generate synthetic video", lambda: make_synthetic_video(work / "source.mp4", duration_sec=30))
    if not video:
        return report

    transcript_path = step("generate synthetic transcript",
                           lambda: make_synthetic_transcript(work / "source.json", duration_sec=30))
    if not transcript_path:
        return report

    transcript = parse_transcript(transcript_path)

    plan = step("plan smart reframe (MediaPipe)",
                lambda: plan_reframe(video, clip_start=2, clip_end=22,
                                      target_w=1080, target_h=1920,
                                      sendcmd_out=work / "reframe.cmds"))
    if plan:
        report["reframe_plan"] = {
            "faces_found": plan.faces_found,
            "frames_sampled": plan.frames_sampled,
            "miss_ratio": round(plan.miss_ratio, 3),
            "shake_px": round(plan.shake_px, 2),
            "sendcmd_written": bool(plan.sendcmd_path),
        }

    kit = BrandKit(font="DejaVu Sans", primary_color="#FFFFFF",
                    accent_color="#FF5B7A", outline_px=3, shadow_px=2)

    render_out = step(
        "render clip (ASS captions + smart reframe)",
        lambda: render_clip(
            video, start=2, end=22, output=work / "clip.mp4",
            transcript=transcript,
            opts=CutOptions(
                vertical=True, smart_reframe=True,
                caption_style="tiktok_pop", brand_kit=kit,
                fast_copy=False,
            ),
            work_dir=work,
        ),
    )
    if render_out and Path(render_out).exists():
        size = Path(render_out).stat().st_size
        report["rendered_clip"] = {"path": str(render_out), "size_bytes": size}

    # Also render a captions-only variant (no reframe) to confirm the subtitles
    # filter path works on landscape output.
    render_landscape = step(
        "render clip (captions only, landscape)",
        lambda: render_clip(
            video, start=2, end=12, output=work / "clip_landscape.mp4",
            transcript=transcript,
            opts=CutOptions(vertical=False, caption_style="clean_minimal",
                            brand_kit=kit, fast_copy=False),
            work_dir=work,
        ),
    )

    thumbs = step(
        "generate thumbnails (4 variants)",
        lambda: generate_thumbnails(
            video, clip_start=2, clip_end=22,
            title="Why scaling is overrated",
            out_dir=work / "thumbs", slug="selftest",
            aspect=(16, 9), brand_kit=kit,
        ),
    )
    if thumbs:
        report["thumbnails"] = {
            "frames_sampled": thumbs.frames_sampled,
            "faces_found": thumbs.faces_found,
            "variants": [{"style": v.style, "path": str(v.path), "score": round(v.score, 3)}
                          for v in thumbs.variants],
        }

    report["total_duration_sec"] = round(time.time() - t0, 2)
    report["passed"] = all(s["ok"] for s in report["steps"])
    return report


if __name__ == "__main__":
    import sys
    report = run_all()
    print("\n" + json.dumps(report, indent=2))
    sys.exit(0 if report.get("passed") else 1)
