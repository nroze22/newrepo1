"""Publish-ready export bundles.

For each clip ID selected, produce a directory (optionally zipped) with:
    <slug>/
        clip.mp4                # rendered clip
        captions.srt            # plain captions
        captions.ass            # styled caption source
        thumbnail.jpg           # hero thumbnail
        thumbnail_alt.jpg       # alternates (if available)
        metadata.json           # titles, captions per platform, hashtags, etc.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .captions import BrandKit, write_ass, write_srt
from .store import Store
from .thumbnails import generate_thumbnails
from .transcript import parse_transcript


@dataclass
class ExportBundle:
    clip_id: int
    directory: Path
    files: list[Path]


def export_clip(
    store: Store,
    clip_id: int,
    *,
    out_dir: Path,
    brand_kit: BrandKit | None = None,
    make_thumbnails: bool = True,
) -> ExportBundle:
    clip = store.get_clip(clip_id)
    if not clip:
        raise KeyError(f"No such clip: {clip_id}")
    if not clip.output_path:
        raise RuntimeError(f"Clip {clip_id} not rendered yet — render it first.")

    slug = f"{clip.video_slug}__{clip_id:04d}"
    folder = Path(out_dir) / slug
    folder.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    # Rendered mp4
    mp4_dest = folder / "clip.mp4"
    shutil.copy2(clip.output_path, mp4_dest)
    files.append(mp4_dest)

    # Captions: from the original transcript around this clip window.
    videos = {v.id: v for v in store.list_videos()}
    video = videos.get(clip.video_id)
    if video:
        transcript = parse_transcript(Path(video.transcript_path))
        srt_path = write_srt(transcript, folder / "captions.srt",
                             clip_start=clip.start_sec, clip_end=clip.end_sec)
        ass_path = write_ass(transcript, folder / "captions.ass",
                             clip_start=clip.start_sec, clip_end=clip.end_sec,
                             brand_kit=brand_kit)
        files.extend([srt_path, ass_path])

    # Thumbnails
    if make_thumbnails and video:
        try:
            thumbs = generate_thumbnails(
                Path(video.video_path), clip.start_sec, clip.end_sec,
                title=clip.title, out_dir=folder, slug="thumbnail",
                brand_kit=brand_kit,
            )
            for v in thumbs.variants:
                files.append(v.path)
        except Exception:
            # Thumbnail generation is best-effort; continue without it.
            pass

    # Metadata
    pkg = store.get_clip_package(clip_id) or {}
    meta = {
        "clip_id": clip_id,
        "video_slug": clip.video_slug,
        "video_source_url": video.source_url if video else None,
        "start_sec": clip.start_sec,
        "end_sec": clip.end_sec,
        "duration_sec": round(clip.end_sec - clip.start_sec, 2),
        "title": clip.title,
        "hook": clip.hook,
        "rationale": clip.rationale,
        "score": clip.score,
        "tags": clip.tags,
        "status": clip.status,
        "package": pkg,
    }
    meta_path = folder / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    files.append(meta_path)

    return ExportBundle(clip_id=clip_id, directory=folder, files=files)


def zip_bundle(bundle: ExportBundle) -> Path:
    zip_path = bundle.directory.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.files:
            zf.write(f, f.name)
    return zip_path


def export_approved(
    store: Store,
    *,
    out_dir: Path,
    brand_kit: BrandKit | None = None,
    video_id: int | None = None,
) -> list[ExportBundle]:
    clips = store.list_clips(video_id=video_id)
    ready = [c for c in clips if c.status == "rendered" or c.status == "approved"]
    return [export_clip(store, c.id, out_dir=out_dir, brand_kit=brand_kit) for c in ready if c.output_path]
