"""Ingest a source (YouTube URL or local path) into the clipper workdir.

Produces a `(video_path, transcript_path)` pair ready for scoring. If no
transcript is found, the transcriber is called as a fallback.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .library import TRANSCRIPT_EXTS, VIDEO_EXTS, LibraryItem, _find_transcript_for


_YT_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/", re.I)


def is_youtube_url(source: str) -> bool:
    return bool(_YT_URL_RE.match(source.strip()))


def is_url(source: str) -> bool:
    return source.strip().lower().startswith(("http://", "https://"))


@dataclass
class IngestResult:
    item: LibraryItem
    transcribed: bool  # True if we had to auto-transcribe (no captions found)


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:80] or "episode"


def _download_youtube(url: str, downloads_dir: Path) -> tuple[Path, Path | None]:
    """Download a YouTube video + its auto-captions (if any). Returns (video, vtt|None)."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is required for YouTube ingest. Install with: pip install yt-dlp"
        ) from e

    downloads_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(downloads_dir / "%(id)s__%(title).80B.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB"],
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    # Resolve final filenames
    video_id = info["id"]
    # Look for the produced mp4 and any matching .vtt
    matches = list(downloads_dir.glob(f"{video_id}__*.*"))
    video = next((p for p in matches if p.suffix.lower() in VIDEO_EXTS), None)
    if not video:
        raise RuntimeError(f"yt-dlp downloaded {url} but no video file was produced")
    vtt = next(
        (p for p in matches if p.suffix.lower() == ".vtt" and ".en" in p.name.lower()),
        None,
    )
    return video, vtt


def ingest_source(
    source: str,
    workdir: Path,
    *,
    transcribe_if_missing: bool = True,
) -> IngestResult:
    """Ingest a single source. Copies/downloads into workdir/sources/ and returns a pair."""
    sources_dir = Path(workdir) / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    if is_youtube_url(source):
        video, transcript = _download_youtube(source, sources_dir)
    elif is_url(source):
        raise ValueError(
            f"Only YouTube URLs are supported for URL ingest. Got: {source}"
        )
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        if src.is_dir():
            raise IsADirectoryError(
                f"{src} is a directory — pass individual files, or use `score` on the directory."
            )
        if src.suffix.lower() not in VIDEO_EXTS:
            raise ValueError(f"Unsupported video extension: {src.suffix}")
        # Copy/symlink the video into sources/ and bring along a sibling transcript if present.
        dest = sources_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        found = _find_transcript_for(src)
        transcript: Path | None = None
        if found:
            transcript = sources_dir / found.name
            if not transcript.exists():
                shutil.copy2(found, transcript)
        video = dest

    transcribed = False
    if transcript is None and transcribe_if_missing:
        from .transcriber import transcribe_video  # local import: optional dep

        transcript = transcribe_video(video, out_dir=sources_dir)
        transcribed = True
    if transcript is None:
        raise RuntimeError(
            f"No transcript for {video.name} and auto-transcription was disabled. "
            f"Set ANTHROPIC/OPENAI keys and retry, or drop a .srt/.vtt next to the video."
        )

    return IngestResult(
        item=LibraryItem(video_path=video, transcript_path=transcript),
        transcribed=transcribed,
    )


def ingest_many(sources: list[str], workdir: Path, **kwargs) -> list[IngestResult]:
    results: list[IngestResult] = []
    for s in sources:
        results.append(ingest_source(s, workdir, **kwargs))
    return results
