"""ffmpeg-powered clipping with animated captions and smart reframe.

Two primary paths:

1. Stream-copy path — fast, lossless. Used when no captions / no reframe /
   no transformation is required.
2. Filter path — re-encode with a composed filter graph:
       [sendcmd ->] crop -> scale -> [subtitles=ass] -> [logo overlay]
   Used whenever captions or vertical reframe are requested.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .captions import BrandKit, CaptionStyle, write_ass
from .reframe import ReframePlan, build_reframe_filter_fragments
from .transcript import Transcript


class FFmpegMissing(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegMissing("ffmpeg not found on PATH — install it to cut clips.")
    return path


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class CutOptions:
    # Orientation
    vertical: bool = False
    smart_reframe: bool = True           # face-track when vertical
    target_w: int = 1080
    target_h: int = 1920

    # Captions
    caption_style: str | None = None     # one of: tiktok_pop, clean_minimal, hype_shadow, news_ticker
    burn_captions: bool = False          # legacy single-headline burn (kept for backcompat)
    caption_text: str = ""

    # Brand + animated captions require a transcript to locate word timings
    brand_kit: BrandKit | None = None

    # Performance
    fast_copy: bool = True               # try stream-copy when no filters needed

    # Internal — populated by render_clip if paths are needed
    ass_path: Path | None = None
    reframe_plan: ReframePlan | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_subtitles_path(p: Path) -> str:
    # ffmpeg subtitles= filter needs escaped : and ' on linux.
    s = str(p)
    s = s.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return s


def _sanitize_for_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("\n", " ")
    )


def _logo_filter(kit: BrandKit | None, target_w: int, target_h: int) -> str | None:
    if not kit or not kit.logo_path:
        return None
    if not Path(kit.logo_path).exists():
        return None
    # Position mapping
    margin = 32
    pos = (kit.logo_position or "top_right").lower()
    coords = {
        "top_left":     f"{margin}:{margin}",
        "top_right":    f"W-w-{margin}:{margin}",
        "bottom_left":  f"{margin}:H-h-{margin}",
        "bottom_right": f"W-w-{margin}:H-h-{margin}",
    }
    xy = coords.get(pos, coords["top_right"])
    alpha = max(0.0, min(1.0, kit.logo_opacity or 0.85))
    # Returns a movie filter string to be appended to the main chain via overlay.
    return f"movie='{_escape_subtitles_path(Path(kit.logo_path))}'[lg];[main][lg]overlay={xy}:format=auto:alpha={alpha}"


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


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

    need_filters = (
        opts.vertical
        or opts.caption_style
        or opts.burn_captions
        or (opts.brand_kit and opts.brand_kit.logo_path)
        or not opts.fast_copy
    )

    cmd: list[str] = [ffmpeg, "-y"]
    # Put -ss *before* -i for a fast keyframe seek; many versions accept minor
    # timestamp drift during the filter path. For stream-copy we keep -ss
    # before -i to avoid re-muxing the whole input.
    cmd += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]

    if not need_filters:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(output)]
        return cmd

    vf_fragments: list[str] = []

    # Reframe + scale
    if opts.vertical:
        if opts.reframe_plan:
            vf_fragments.extend(build_reframe_filter_fragments(opts.reframe_plan))
        else:
            # Dumb center-crop fallback when we don't have a plan.
            vf_fragments.append("crop=ih*9/16:ih")
            vf_fragments.append(f"scale={opts.target_w}:{opts.target_h}:force_original_aspect_ratio=decrease")
            vf_fragments.append(f"pad={opts.target_w}:{opts.target_h}:(ow-iw)/2:(oh-ih)/2:color=black")

    # Animated captions (via ASS file)
    if opts.caption_style and opts.ass_path:
        vf_fragments.append(f"subtitles='{_escape_subtitles_path(opts.ass_path)}'")

    # Legacy headline burn
    if opts.burn_captions and opts.caption_text and not opts.caption_style:
        text = _sanitize_for_drawtext(opts.caption_text)
        vf_fragments.append(
            "drawtext=text='" + text + "':fontcolor=white:fontsize=42:box=1:"
            "boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y=h-(text_h*2)-80"
        )

    # Logo overlay via filter_complex (overlay needs two inputs).
    logo_chain = _logo_filter(opts.brand_kit, opts.target_w, opts.target_h)

    if logo_chain:
        # Build as filter_complex: [0:v]<vf>,[main]; then overlay logo.
        vf_chain = ",".join(vf_fragments) if vf_fragments else "null"
        filter_complex = f"[0:v]{vf_chain}[main];{logo_chain}"
        cmd += ["-filter_complex", filter_complex]
    else:
        if vf_fragments:
            cmd += ["-vf", ",".join(vf_fragments)]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output),
    ]
    return cmd


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


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


def render_clip(
    source: Path,
    start: float,
    end: float,
    output: Path,
    *,
    transcript: Transcript | None = None,
    opts: CutOptions | None = None,
    work_dir: Path | None = None,
) -> Path:
    """High-level wrapper that prepares caption ASS and reframe plan as needed."""
    opts = opts or CutOptions()
    work_dir = Path(work_dir or Path(output).parent / ".work")
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) Build ASS captions if an animated style is requested.
    if opts.caption_style and transcript is not None:
        ass_path = work_dir / (Path(output).stem + ".ass")
        play_w = opts.target_w if opts.vertical else 1920
        play_h = opts.target_h if opts.vertical else 1080
        write_ass(
            transcript,
            ass_path,
            clip_start=start, clip_end=end,
            style=opts.caption_style,
            brand_kit=opts.brand_kit,
            play_w=play_w, play_h=play_h,
        )
        opts.ass_path = ass_path

    # 2) Plan smart reframe if vertical and enabled.
    if opts.vertical and opts.smart_reframe and opts.reframe_plan is None:
        try:
            from .reframe import plan_reframe  # local import — mediapipe may not be installed
            sendcmd = work_dir / (Path(output).stem + ".reframe.cmds")
            opts.reframe_plan = plan_reframe(
                Path(source),
                clip_start=start, clip_end=end,
                target_w=opts.target_w, target_h=opts.target_h,
                sendcmd_out=sendcmd,
            )
        except Exception:
            opts.reframe_plan = None

    # 3) Force re-encode if we're applying filters.
    if opts.caption_style or (opts.vertical and opts.reframe_plan) or (opts.brand_kit and opts.brand_kit.logo_path):
        opts.fast_copy = False

    return cut_clip(Path(source), start, end, Path(output), opts)
