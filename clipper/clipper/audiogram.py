"""Audiogram renderer for audio-only podcasts.

When a source is audio-only (no usable video stream) we render a branded
vertical frame — static background + animated waveform bar + burned ASS
captions — via a single ffmpeg pass. Output is 1080x1920 mp4 suitable for
Shorts / Reels / TikTok.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .captions import BrandKit, write_ass
from .transcript import Transcript


def is_audio_only(video_path: Path) -> bool:
    """Cheap probe: does this source carry a usable video stream?"""
    ff = shutil.which("ffprobe")
    if not ff:
        return False
    proc = subprocess.run(
        [ff, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type,width,height",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    out = (proc.stdout or "").strip()
    return not out or "video" not in out


def _sanitize_for_drawtext(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(":", r"\:")
            .replace("'", "\u2019").replace("%", r"\%"))


def render_audiogram(
    source: Path,
    start: float,
    end: float,
    output: Path,
    *,
    transcript: Transcript | None = None,
    title: str = "",
    subtitle: str = "",
    brand_kit: BrandKit | None = None,
    caption_style: str | None = "tiktok_pop",
    target_w: int = 1080,
    target_h: int = 1920,
    work_dir: Path | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required for audiogram render")
    kit = brand_kit or BrandKit()
    work_dir = Path(work_dir or output.parent / ".work")
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end - start)

    # ASS captions (if transcript + style set).
    ass_path: Path | None = None
    if caption_style and transcript is not None:
        ass_path = work_dir / (output.stem + ".ass")
        write_ass(transcript, ass_path, clip_start=start, clip_end=end,
                  style=caption_style, brand_kit=kit,
                  play_w=target_w, play_h=target_h)

    primary = kit.primary_color or "#FFFFFF"
    accent = kit.accent_color or "#FF5B7A"
    # Parse colors to hex without '#'
    p_hex = primary.lstrip("#")
    a_hex = accent.lstrip("#")

    # Build the filter graph:
    #   background (solid gradient via geq), showwaves overlay in the middle,
    #   optional title + subtitle via drawtext, ASS subtitles burned on top.
    bg = f"color=c=0x0b0d12:s={target_w}x{target_h}:d={duration}"
    # showwaves gives a horizontal wavy line we scale to a wide band.
    waves = (
        f"showwaves=s=800x240:mode=cline:rate=25:colors=0x{a_hex}"
        ",format=rgba,colorchannelmixer=aa=0.95"
    )

    title_drawtext = ""
    if title:
        t = _sanitize_for_drawtext(title[:80])
        title_drawtext = (
            f",drawtext=text='{t}':fontcolor=0x{p_hex}:fontsize=54:"
            f"x=(w-text_w)/2:y=h*0.24:box=0"
        )
    subtitle_drawtext = ""
    if subtitle:
        s = _sanitize_for_drawtext(subtitle[:120])
        subtitle_drawtext = (
            f",drawtext=text='{s}':fontcolor=0x{a_hex}:fontsize=32:"
            f"x=(w-text_w)/2:y=h*0.30:box=0"
        )

    subs_filter = ""
    if ass_path:
        path_esc = str(ass_path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        subs_filter = f",subtitles='{path_esc}'"

    # Main graph: generate a solid dark background, overlay the waveform bar
    # centered, add title + burned captions.
    filter_complex = (
        f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,{waves}[w];"
        f"{bg}[bg];"
        f"[bg][w]overlay=(W-w)/2:(H-h)/2"
        f"{title_drawtext}{subtitle_drawtext}{subs_filter}"
    )

    cmd = [
        ffmpeg, "-y",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg audiogram failed ("
            + str(proc.returncode) + "): "
            + proc.stderr[-800:]
        )
    return Path(output)
