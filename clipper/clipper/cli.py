"""CLI: ingest, score, render, serve.

Typical one-shot flow:

    clipper run https://youtube.com/watch?v=... /path/to/episode.mp4
    clipper serve                         # open http://127.0.0.1:8765
    clipper render --approved             # batch-render approved clips

Or broken into steps:

    clipper ingest <url-or-path> ...
    clipper score                         # defaults to <workdir>/sources
    clipper serve
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .cutter import CutOptions, cut_clip
from .ingester import IngestResult, SourceMeta, ingest_source
from .library import LibraryItem, scan_library
from .produce import produce_clips
from .server import serve
from .store import Store
from .transcript import parse_transcript


def _paths(workdir: Path) -> tuple[Path, Path, Path]:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "sources").mkdir(exist_ok=True)
    (workdir / "clips").mkdir(exist_ok=True)
    return workdir / "clipper.db", workdir / "clips", workdir / "sources"


def _produce_items(
    store: Store,
    items: list[tuple[LibraryItem, SourceMeta | None]],
    *,
    model: str,
    max_clips: int,
    min_score: int,
) -> None:
    print(f"Running multi-agent pipeline on {len(items)} episode(s)…")
    for item, meta in items:
        transcript = parse_transcript(item.transcript_path)
        duration = transcript.duration
        video_id = store.upsert_video(
            item.slug, item.video_path, item.transcript_path, duration,
            source_url=(meta.source_url if meta else None),
            title=(meta.title if meta else None),
            channel=(meta.channel if meta else None),
        )
        print(f"  • {item.slug} ({duration/60:.1f} min)")

        def progress(pct: float, msg: str) -> None:
            print(f"    [{int(pct*100):3d}%] {msg}")

        result = produce_clips(
            transcript, meta,
            video_slug=item.slug,
            max_clips=max_clips, min_score=min_score,
            models={"scout": model},
            progress_cb=progress,
        )
        store.replace_clips(video_id, result.clips)
        persisted = store.list_clips(video_id=video_id)
        for clip_row, package in zip(persisted, result.packages):
            store.save_clip_package(clip_row.id, package.to_dict())
        coverage = result.critic.coverage_note if result.critic else ""
        store.save_brief(video_id, result.brief.to_dict(), coverage_note=coverage)
        top = max((c.score for c in result.clips), default=0)
        print(f"    → {len(result.clips)} clip(s), top score {top}, domain={result.brief.domain or '?'}")


def cmd_ingest(args: argparse.Namespace) -> int:
    _, _, sources_dir = _paths(Path(args.workdir))
    any_transcribed = False
    for src in args.sources:
        print(f"Ingesting: {src}")
        result = ingest_source(src, Path(args.workdir), transcribe_if_missing=not args.no_transcribe)
        any_transcribed = any_transcribed or result.transcribed
        marker = "(auto-transcribed)" if result.transcribed else "(captions found)"
        print(f"  → {result.item.video_path.name} + {result.item.transcript_path.name} {marker}")
    print(f"\nIngested into: {sources_dir}")
    if any_transcribed:
        print("Tip: Whisper JSON with word-level timings produces the cleanest cuts.")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    db_path, _, sources_dir = _paths(Path(args.workdir))
    root = Path(args.library).expanduser().resolve() if args.library else sources_dir
    items = scan_library(root)
    if not items:
        print(f"No videos with transcripts found under {root}", file=sys.stderr)
        print("  Hint: run `clipper ingest <source>` first, or pass an existing library path.", file=sys.stderr)
        return 1
    store = Store(db_path)
    _produce_items(
        store,
        [(i, None) for i in items],
        model=args.model, max_clips=args.max_clips, min_score=args.min_score,
    )
    print(f"\nDone. Review at: clipper serve --workdir {args.workdir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Ingest one or more sources, then run the full agent pipeline."""
    db_path, _, _ = _paths(Path(args.workdir))
    items: list[tuple[LibraryItem, SourceMeta | None]] = []
    for src in args.sources:
        print(f"Ingesting: {src}")
        result = ingest_source(src, Path(args.workdir), transcribe_if_missing=not args.no_transcribe)
        caps = result.meta.captions_source or "unknown"
        print(f"  → {result.item.video_path.name} · captions: {caps}")
        items.append((result.item, result.meta))
    store = Store(db_path)
    _produce_items(store, items, model=args.model, max_clips=args.max_clips, min_score=args.min_score)
    print(f"\nDone. Review at: clipper serve --workdir {args.workdir}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    db_path, output_dir, _ = _paths(Path(args.workdir))
    store = Store(db_path)
    status = "approved" if args.approved else None
    clips = store.list_clips(status=status)
    if args.video:
        clips = [c for c in clips if c.video_slug == args.video]
    if not clips:
        print("No clips to render.", file=sys.stderr)
        return 1
    opts = CutOptions(
        vertical=args.vertical,
        burn_captions=args.captions,
        fast_copy=not (args.vertical or args.captions),
    )
    for c in clips:
        out = output_dir / f"{c.video_slug}__{c.id:04d}.mp4"
        opts.caption_text = c.title or c.hook
        print(f"  → {out.name} ({c.start_sec:.1f}-{c.end_sec:.1f}s, score={c.score})")
        cut_clip(Path(c.video_path), c.start_sec, c.end_sec, out, opts)
        store.update_clip(c.id, status="rendered", output_path=str(out))
    print(f"\nRendered {len(clips)} clip(s) to {output_dir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    db_path, output_dir, _ = _paths(Path(args.workdir))
    print(f"Serving review UI at http://{args.host}:{args.port}")
    print(f"  db:     {db_path}")
    print(f"  output: {output_dir}")
    serve(db_path=db_path, output_dir=output_dir, host=args.host, port=args.port)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose the environment: deps, API keys, and run the media-pipeline selftest."""
    import json as _json, shutil as _shutil
    from . import selftest

    print("Clipper doctor\n" + "=" * 60)

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, note: str = ""):
        status = "✓" if ok else "✗"
        print(f"  {status}  {name}{'  — ' + note if note else ''}")
        checks.append((name, ok, note))

    # Binaries
    ffmpeg = _shutil.which("ffmpeg")
    check("ffmpeg on PATH", bool(ffmpeg), ffmpeg or "install via: apt install ffmpeg / brew install ffmpeg")
    ytdlp = _shutil.which("yt-dlp") or True  # yt-dlp is a pip package too
    check("yt-dlp importable", _can_import("yt_dlp"))

    # Python libs
    for lib in ["anthropic", "openai", "fastapi", "uvicorn", "jinja2",
                "mediapipe", "cv2", "PIL", "numpy", "tiktoken"]:
        check(f"python lib: {lib}", _can_import(lib))

    # API keys
    import os as _os
    check("ANTHROPIC_API_KEY set", bool(_os.environ.get("ANTHROPIC_API_KEY")),
          "required for agent pipeline")
    check("OPENAI_API_KEY set", bool(_os.environ.get("OPENAI_API_KEY")),
          "optional — needed for Whisper fallback + semantic search")

    # Quick pytest-style media selftest
    print("\nMedia pipeline selftest (synthetic 30s episode)…")
    report = selftest.run_all(verbose=True)
    check("End-to-end media pipeline", bool(report.get("passed")), f"work_dir={report.get('work_dir')}")

    print("\nSummary:")
    failed = [n for n, ok, _ in checks if not ok]
    if failed:
        print(f"  FAILED: {len(failed)} check(s) — " + ", ".join(failed))
        return 1
    print(f"  All {len(checks)} checks passed.")
    return 0


def _can_import(mod: str) -> bool:
    import importlib
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def cmd_list(args: argparse.Namespace) -> int:
    db_path, _, _ = _paths(Path(args.workdir))
    store = Store(db_path)
    for v in store.list_videos():
        clips = store.list_clips(video_id=v.id)
        approved = sum(1 for c in clips if c.status == "approved")
        rendered = sum(1 for c in clips if c.status == "rendered")
        top = max((c.score for c in clips), default=0)
        print(f"{v.slug}: {len(clips)} clips, top={top}, approved={approved}, rendered={rendered}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="AI podcast transcript clipper")
    p.add_argument(
        "--workdir",
        default=os.environ.get("CLIPPER_WORKDIR", ".clipper"),
        help="Where sources, db, and rendered clips live (default: .clipper)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="Pull YouTube URLs or local files into the workdir")
    pi.add_argument("sources", nargs="+", help="YouTube URL(s) or local video file path(s)")
    pi.add_argument("--no-transcribe", action="store_true", help="Don't auto-transcribe missing transcripts")
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("score", help="Score clip candidates for everything in the workdir (or a given library)")
    ps.add_argument("library", nargs="?", help="Optional library root (defaults to <workdir>/sources)")
    ps.add_argument("--model", default="claude-sonnet-4-6")
    ps.add_argument("--max-clips", type=int, default=10)
    ps.add_argument("--min-score", type=int, default=70)
    ps.set_defaults(func=cmd_score)

    pr = sub.add_parser("run", help="One-shot: ingest sources AND score them")
    pr.add_argument("sources", nargs="+", help="YouTube URL(s) or local video file path(s)")
    pr.add_argument("--no-transcribe", action="store_true")
    pr.add_argument("--model", default="claude-sonnet-4-6")
    pr.add_argument("--max-clips", type=int, default=10)
    pr.add_argument("--min-score", type=int, default=70)
    pr.set_defaults(func=cmd_run)

    prn = sub.add_parser("render", help="Render clips with ffmpeg (headless, no review UI)")
    prn.add_argument("--approved", action="store_true", help="Only render clips marked approved")
    prn.add_argument("--video", help="Limit to a single video slug")
    prn.add_argument("--vertical", action="store_true", help="Crop to 9:16")
    prn.add_argument("--captions", action="store_true", help="Burn title as caption")
    prn.set_defaults(func=cmd_render)

    pv = sub.add_parser("serve", help="Launch the review UI")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=8765)
    pv.set_defaults(func=cmd_serve)

    pl = sub.add_parser("list", help="Print a summary of scored videos")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("doctor", help="Check deps, API keys, and run the end-to-end media selftest")
    pd.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
