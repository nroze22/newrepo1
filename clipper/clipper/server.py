"""FastAPI review UI: approve, adjust, and render clips."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .cutter import CutOptions, cut_clip
from .store import ClipRow, Store
from .transcript import format_timestamp

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.filters["ts"] = format_timestamp


def create_app(db_path: Path, output_dir: Path) -> FastAPI:
    store = Store(db_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Podcast Clipper")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        videos = store.list_videos()
        summary = []
        for v in videos:
            clips = store.list_clips(video_id=v.id)
            summary.append(
                {
                    "video": v,
                    "total": len(clips),
                    "approved": sum(1 for c in clips if c.status == "approved"),
                    "rendered": sum(1 for c in clips if c.status == "rendered"),
                    "top_score": max((c.score for c in clips), default=0),
                }
            )
        return TEMPLATES.TemplateResponse(
            request, "index.html", {"videos": summary}
        )

    @app.get("/videos/{video_id}", response_class=HTMLResponse)
    def video_detail(request: Request, video_id: int):
        videos = {v.id: v for v in store.list_videos()}
        if video_id not in videos:
            raise HTTPException(404, "Video not found")
        clips = store.list_clips(video_id=video_id)
        return TEMPLATES.TemplateResponse(
            request, "video.html", {"video": videos[video_id], "clips": clips}
        )

    @app.post("/clips/{clip_id}/approve")
    def approve(clip_id: int):
        store.update_clip(clip_id, status="approved")
        return JSONResponse({"ok": True, "status": "approved"})

    @app.post("/clips/{clip_id}/reject")
    def reject(clip_id: int):
        store.update_clip(clip_id, status="rejected")
        return JSONResponse({"ok": True, "status": "rejected"})

    @app.post("/clips/{clip_id}/update")
    def update(
        clip_id: int,
        start: float = Form(...),
        end: float = Form(...),
        title: str = Form(""),
    ):
        if end <= start:
            raise HTTPException(400, "end must be greater than start")
        store.update_clip(clip_id, start=start, end=end, title=title or None)
        return JSONResponse({"ok": True})

    @app.post("/clips/{clip_id}/render")
    def render(
        clip_id: int,
        vertical: bool = Form(False),
        burn_captions: bool = Form(False),
    ):
        clip = store.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        out = output_dir / f"{clip.video_slug}__{clip_id:04d}.mp4"
        opts = CutOptions(
            vertical=vertical,
            burn_captions=burn_captions,
            fast_copy=not (vertical or burn_captions),
            caption_text=clip.title or clip.hook,
        )
        cut_clip(Path(clip.video_path), clip.start_sec, clip.end_sec, out, opts)
        store.update_clip(clip_id, status="rendered", output_path=str(out))
        return JSONResponse({"ok": True, "output": str(out)})

    @app.post("/videos/{video_id}/render-approved")
    def render_approved(
        video_id: int,
        vertical: bool = Form(False),
        burn_captions: bool = Form(False),
    ):
        approved = [c for c in store.list_clips(video_id=video_id) if c.status == "approved"]
        rendered: list[str] = []
        for clip in approved:
            out = output_dir / f"{clip.video_slug}__{clip.id:04d}.mp4"
            opts = CutOptions(
                vertical=vertical,
                burn_captions=burn_captions,
                fast_copy=not (vertical or burn_captions),
                caption_text=clip.title or clip.hook,
            )
            cut_clip(Path(clip.video_path), clip.start_sec, clip.end_sec, out, opts)
            store.update_clip(clip.id, status="rendered", output_path=str(out))
            rendered.append(str(out))
        return JSONResponse({"ok": True, "rendered": rendered, "count": len(rendered)})

    @app.get("/media/video/{video_id}")
    def stream_source(video_id: int, request: Request):
        videos = {v.id: v for v in store.list_videos()}
        if video_id not in videos:
            raise HTTPException(404, "Not found")
        return _range_response(request, Path(videos[video_id].video_path))

    @app.get("/media/clip/{clip_id}")
    def clip_file(clip_id: int):
        clip = store.get_clip(clip_id)
        if not clip or not clip.output_path:
            raise HTTPException(404, "Not rendered yet")
        return FileResponse(clip.output_path, media_type="video/mp4")

    return app


def _range_response(request: Request, path: Path) -> StreamingResponse | FileResponse:
    """Serve a local video with HTTP Range support so the browser can seek."""
    if not path.exists():
        raise HTTPException(404, f"Source missing: {path}")
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    if not range_header:
        return FileResponse(str(path), media_type=mime)
    units, _, rng = range_header.partition("=")
    if units.strip() != "bytes":
        raise HTTPException(416, "Only byte ranges supported")
    start_s, _, end_s = rng.partition("-")
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else file_size - 1
    end = min(end, file_size - 1)
    length = end - start + 1

    def iterfile():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(iterfile(), status_code=206, headers=headers, media_type=mime)


def serve(db_path: Path, output_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    app = create_app(db_path, output_dir)
    uvicorn.run(app, host=host, port=port)
