"""CLI: scan a library, score candidates, serve the review UI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .library import scan_library
from .scorer import score_transcript
from .server import serve
from .store import Store
from .transcript import parse_transcript


def _default_paths(workdir: Path) -> tuple[Path, Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir / "clipper.db", workdir / "clips"


def cmd_score(args: argparse.Namespace) -> int:
    root = Path(args.library).expanduser().resolve()
    db_path, _ = _default_paths(Path(args.workdir))
    items = scan_library(root)
    if not items:
        print(f"No videos with transcripts found under {root}", file=sys.stderr)
        return 1
    store = Store(db_path)
    print(f"Found {len(items)} videos. Scoring with model={args.model}…")
    for item in items:
        transcript = parse_transcript(item.transcript_path)
        duration = transcript.duration
        video_id = store.upsert_video(item.slug, item.video_path, item.transcript_path, duration)
        print(f"  • {item.slug} ({duration/60:.1f} min) → ", end="", flush=True)
        clips = score_transcript(
            transcript,
            video_slug=item.slug,
            model=args.model,
            max_clips=args.max_clips,
            min_score=args.min_score,
        )
        store.replace_clips(video_id, clips)
        print(f"{len(clips)} candidates (top {max((c.score for c in clips), default=0)})")
    print(f"\nDone. Review at: python -m clipper.cli serve --workdir {args.workdir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    db_path, output_dir = _default_paths(Path(args.workdir))
    print(f"Serving review UI at http://{args.host}:{args.port}")
    print(f"  db:     {db_path}")
    print(f"  output: {output_dir}")
    serve(db_path=db_path, output_dir=output_dir, host=args.host, port=args.port)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db_path, _ = _default_paths(Path(args.workdir))
    store = Store(db_path)
    for v in store.list_videos():
        clips = store.list_clips(video_id=v.id)
        print(f"{v.slug}: {len(clips)} clips, top={max((c.score for c in clips), default=0)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="AI podcast transcript clipper")
    p.add_argument("--workdir", default=os.environ.get("CLIPPER_WORKDIR", ".clipper"),
                   help="Where the SQLite db and rendered clips live (default: .clipper)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("score", help="Scan a library and score clip candidates")
    ps.add_argument("library", help="Path to a directory of videos+transcripts")
    ps.add_argument("--model", default="claude-sonnet-4-6")
    ps.add_argument("--max-clips", type=int, default=10)
    ps.add_argument("--min-score", type=int, default=70)
    ps.set_defaults(func=cmd_score)

    pv = sub.add_parser("serve", help="Launch the review UI")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=8765)
    pv.set_defaults(func=cmd_serve)

    pl = sub.add_parser("list", help="Print a summary of scored videos")
    pl.set_defaults(func=cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
