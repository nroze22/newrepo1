"""ffmpeg-powered clipping with optional vertical reformat and burned captions."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegMissing(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegMissing("ffmpeg not found on PATH — install it to cut clips.")
    return path


@dataclass
class CutOptions:
    vertical: bool = False            # crop to 9:16
    burn_captions: bool = False       # burn caption text over the clip
    fast_copy: bool = True            # -c copy (fast, no re-encode) when safe
    caption_text: str = ""            # used when burn_captions=True


def _sanitize_for_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("\n", " ")
    )


def build_command(
    ffmpeg: str,
    source: Path,
    start: float,
    end: float,
    output: Path,
    opts: CutOptions,
) -> list[str]:
    duration = max(0.1, end - start)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]

    need_reencode = opts.vertical or opts.burn_captions or not opts.fast_copy

    if not need_reencode:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(output)]
        return cmd

    vf: list[str] = []
    if opts.vertical:
        # Crop to a centered 9:16 window, then pad (in case source is already vertical).
        vf.append("crop=ih*9/16:ih")
        vf.append("scale=1080:1920:force_original_aspect_ratio=decrease")
        vf.append("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black")
    if opts.burn_captions and opts.caption_text:
        text = _sanitize_for_drawtext(opts.caption_text)
        vf.append(
            "drawtext=text='" + text + "':"
            "fontcolor=white:fontsize=42:box=1:boxcolor=black@0.55:boxborderw=16:"
            "x=(w-text_w)/2:y=h-(text_h*2)-80"
        )
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output),
    ]
    return cmd


def cut_clip(
    source: Path,
    start: float,
    end: float,
    output: Path,
    opts: CutOptions | None = None,
) -> Path:
    ffmpeg = require_ffmpeg()
    opts = opts or CutOptions()
    cmd = build_command(ffmpeg, Path(source), start, end, Path(output), opts)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(p) for p in cmd)}\n"
            f"  stderr: {proc.stderr[-800:]}"
        )
    return Path(output)
