"""Scan a directory of podcast videos and pair each with a transcript."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
TRANSCRIPT_EXTS = (".srt", ".vtt", ".json", ".txt")


@dataclass
class LibraryItem:
    video_path: Path
    transcript_path: Path

    @property
    def slug(self) -> str:
        return self.video_path.stem


def _find_transcript_for(video: Path) -> Path | None:
    # 1) Same-stem sibling (episode.mp4 + episode.srt)
    for ext in TRANSCRIPT_EXTS:
        candidate = video.with_suffix(ext)
        if candidate.exists():
            return candidate
    # 2) transcripts/ subdir next to the video
    sibling_dir = video.parent / "transcripts"
    if sibling_dir.is_dir():
        for ext in TRANSCRIPT_EXTS:
            candidate = sibling_dir / (video.stem + ext)
            if candidate.exists():
                return candidate
    return None


def scan_library(root: Path) -> list[LibraryItem]:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Library root not found: {root}")
    items: list[LibraryItem] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in VIDEO_EXTS or not path.is_file():
            continue
        transcript = _find_transcript_for(path)
        if transcript is None:
            continue
        items.append(LibraryItem(video_path=path, transcript_path=transcript))
    return items
