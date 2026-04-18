"""Ingest a source (YouTube URL or local path) into the clipper workdir.

Produces a `(video_path, transcript_path, meta)` triple ready for scoring.
If no transcript is found, the transcriber is called as a fallback.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .library import TRANSCRIPT_EXTS, VIDEO_EXTS, LibraryItem, _find_transcript_for


_YT_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", re.I)


def is_youtube_url(source: str) -> bool:
    return bool(_YT_URL_RE.match(source.strip()))


def is_url(source: str) -> bool:
    return source.strip().lower().startswith(("http://", "https://"))


@dataclass
class SourceMeta:
    """Extra context about a source — fed to the producer agent to inform the brief."""

    source_url: str = ""
    title: str = ""
    uploader: str = ""
    channel: str = ""
    channel_description: str = ""
    video_description: str = ""
    duration_sec: float = 0.0
    upload_date: str = ""
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    view_count: int = 0
    captions_source: str = ""  # 'uploader' | 'auto' | 'whisper' | 'sidecar'


@dataclass
class IngestResult:
    item: LibraryItem
    meta: SourceMeta
    transcribed: bool  # True if we had to auto-transcribe (no captions found)


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:80] or "episode"


def _pick_subtitle(matches: list[Path], info: dict) -> tuple[Path | None, str]:
    """Prefer uploader-provided subs over auto-captions; match EN variants flexibly."""
    # What did yt-dlp actually place on disk?  Group them.
    vtts = [p for p in matches if p.suffix.lower() == ".vtt"]
    if not vtts:
        return None, ""

    en_langs = info.get("subtitles") or {}
    en_auto = info.get("automatic_captions") or {}
    has_uploader_en = any(k.lower().startswith("en") for k in en_langs)
    has_auto_en = any(k.lower().startswith("en") for k in en_auto)

    # yt-dlp writes the lang into the filename like "<id>__<title>.en.vtt".
    en_vtts = [p for p in vtts if re.search(r"\.en(?:[-_][A-Za-z0-9]+)?\.vtt$", p.name, re.I)]
    if en_vtts:
        # Prefer non-auto when both present. Auto sub files often include "en-orig" or are
        # the only option. Without additional signals, just take the first EN vtt.
        vtt = en_vtts[0]
        source_kind = "uploader" if has_uploader_en else ("auto" if has_auto_en else "uploader")
        return vtt, source_kind
    # Fall back to any vtt present (some channels only ship a default lang).
    return vtts[0], "uploader"


def _download_youtube(
    url: str,
    downloads_dir: Path,
    *,
    on_progress: Callable[[float, str], None] | None = None,
) -> tuple[Path, Path | None, SourceMeta]:
    """Download a YouTube video + its captions (uploader-preferred, auto fallback)."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is required for YouTube ingest. Install with: pip install yt-dlp"
        ) from e

    downloads_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(downloads_dir / "%(id)s__%(title).80B.%(ext)s")

    def hook(d):
        if not on_progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total) if total else 0.0
            on_progress(pct, f"Downloading {d.get('filename', '')} ({downloaded/1e6:.1f}MB)")
        elif d.get("status") == "finished":
            on_progress(1.0, "Download complete, post-processing…")

    cookies = os.environ.get("YT_COOKIES")  # optional path to cookies.txt
    ydl_opts: dict = {
        "outtmpl": outtmpl,
        # Explicit container preferences + robust fallbacks. We prefer mp4 so
        # ffmpeg stream-copy can clip without re-encode.
        "format": (
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
            "bv*[height<=1080]+ba/"
            "best[height<=1080]/"
            "best"
        ),
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en", "en-US", "en-GB", "en-orig"],
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [hook],
        "ignoreerrors": False,
    }
    if cookies and Path(cookies).exists():
        ydl_opts["cookiefile"] = cookies

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        msg = str(e)
        if "Sign in to confirm your age" in msg or "members-only" in msg.lower():
            raise RuntimeError(
                f"YouTube requires login for {url}. Set YT_COOKIES=/path/to/cookies.txt "
                f"(export from your browser) and retry."
            ) from e
        if "Private video" in msg or "unavailable" in msg.lower():
            raise RuntimeError(f"YouTube says this video is not available: {url}") from e
        raise

    video_id = info["id"]
    matches = list(downloads_dir.glob(f"{video_id}__*.*"))
    video = next((p for p in matches if p.suffix.lower() in VIDEO_EXTS), None)
    if not video:
        raise RuntimeError(f"yt-dlp downloaded {url} but no video file was produced")
    vtt, caption_source = _pick_subtitle(matches, info)

    meta = SourceMeta(
        source_url=url,
        title=info.get("title") or "",
        uploader=info.get("uploader") or info.get("uploader_id") or "",
        channel=info.get("channel") or "",
        channel_description=(info.get("channel_description") or info.get("uploader_description") or "")[:2000],
        video_description=(info.get("description") or "")[:4000],
        duration_sec=float(info.get("duration") or 0.0),
        upload_date=info.get("upload_date") or "",
        tags=list(info.get("tags") or [])[:25],
        categories=list(info.get("categories") or [])[:5],
        view_count=int(info.get("view_count") or 0),
        captions_source=caption_source if vtt else "",
    )
    return video, vtt, meta


def ingest_source(
    source: str,
    workdir: Path,
    *,
    transcribe_if_missing: bool = True,
    on_progress: Callable[[float, str], None] | None = None,
) -> IngestResult:
    """Ingest a single source. Copies/downloads into workdir/sources/ and returns a pair."""
    sources_dir = Path(workdir) / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    if is_youtube_url(source):
        video, transcript, meta = _download_youtube(source, sources_dir, on_progress=on_progress)
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
        dest = sources_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        found = _find_transcript_for(src)
        transcript = None
        if found:
            transcript = sources_dir / found.name
            if not transcript.exists():
                shutil.copy2(found, transcript)
        video = dest
        meta = SourceMeta(
            title=dest.stem,
            captions_source="sidecar" if transcript else "",
        )

    transcribed = False
    if transcript is None and transcribe_if_missing:
        if on_progress:
            on_progress(0.0, "No captions found — running Whisper transcription…")
        from .transcriber import transcribe_video  # local import: optional dep

        transcript = transcribe_video(video, out_dir=sources_dir)
        transcribed = True
        meta.captions_source = "whisper"
    if transcript is None:
        raise RuntimeError(
            f"No transcript for {video.name} and auto-transcription was disabled. "
            f"Set ANTHROPIC/OPENAI keys and retry, or drop a .srt/.vtt next to the video."
        )

    return IngestResult(
        item=LibraryItem(video_path=video, transcript_path=transcript),
        meta=meta,
        transcribed=transcribed,
    )


def ingest_many(sources: list[str], workdir: Path, **kwargs) -> list[IngestResult]:
    return [ingest_source(s, workdir, **kwargs) for s in sources]
