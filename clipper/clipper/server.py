"""FastAPI review UI: add sources, watch progress, review, give feedback, render."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .cutter import CutOptions, cut_clip
from .ingester import ingest_source, is_youtube_url
from .jobs import JobContext, LogLine, manager as job_manager
from .library import LibraryItem
from .preferences import distill_taste_profile
from .scorer import score_transcript
from .store import ClipRow, Store
from .transcript import format_timestamp, parse_transcript

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.filters["ts"] = format_timestamp


def _score_one(store: Store, item: LibraryItem, *, model: str, max_clips: int, min_score: int, taste: str, ctx: JobContext, base: float, span: float) -> int:
    transcript = parse_transcript(item.transcript_path)
    duration = transcript.duration
    video_id = store.upsert_video(item.slug, item.video_path, item.transcript_path, duration)

    def cb(done: int, total: int) -> None:
        pct = base + span * (done / max(total, 1))
        ctx.progress(pct, f"{item.slug}: window {done}/{total}")

    clips = score_transcript(
        transcript,
        video_slug=item.slug,
        model=model,
        max_clips=max_clips,
        min_score=min_score,
        taste_profile=taste,
        progress_cb=cb,
    )
    store.replace_clips(video_id, clips)
    ctx.info(f"{item.slug}: {len(clips)} candidates (top {max((c.score for c in clips), default=0)})")
    return video_id


def create_app(db_path: Path, output_dir: Path, workdir: Path | None = None) -> FastAPI:
    store = Store(db_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir) if workdir else Path(db_path).parent

    app = FastAPI(title="Podcast Clipper")

    @app.on_event("startup")
    async def _bind() -> None:
        job_manager.bind_loop(asyncio.get_event_loop())

    # --- Pages -----------------------------------------------------------------
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
                    "rejected": sum(1 for c in clips if c.status == "rejected"),
                    "top_score": max((c.score for c in clips), default=0),
                }
            )
        stats = store.feedback_stats()
        totals = {
            "videos": len(videos),
            "clips": sum(s["total"] for s in summary),
            "approved": sum(s["approved"] for s in summary),
            "rendered": sum(s["rendered"] for s in summary),
            "feedback": sum(stats.values()),
        }
        taste = store.get_taste_profile()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"videos": summary, "totals": totals, "has_taste": bool(taste.strip())},
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

    @app.get("/add", response_class=HTMLResponse)
    def add_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "add.html", {})

    @app.get("/preferences", response_class=HTMLResponse)
    def prefs_page(request: Request):
        taste = store.get_taste_profile()
        stats = store.feedback_stats()
        feedback = store.list_feedback(limit=50)
        return TEMPLATES.TemplateResponse(
            request,
            "preferences.html",
            {"taste": taste, "stats": stats, "feedback": feedback},
        )

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "jobs.html", {"jobs": job_manager.list()})

    # --- Ingest / score jobs ---------------------------------------------------
    @app.post("/api/ingest")
    async def ingest_api(
        urls: str = Form(""),
        model: str = Form("claude-sonnet-4-6"),
        max_clips: int = Form(10),
        min_score: int = Form(70),
        use_taste: bool = Form(True),
        file: UploadFile | None = File(None),
    ):
        url_list = [u.strip() for u in urls.splitlines() if u.strip()]
        local_paths: list[str] = []
        if file is not None and file.filename:
            sources_dir = workdir / "sources"
            sources_dir.mkdir(parents=True, exist_ok=True)
            dest = sources_dir / Path(file.filename).name
            with dest.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            local_paths.append(str(dest))
        sources = url_list + local_paths
        if not sources:
            raise HTTPException(400, "Provide at least one YouTube URL or upload a file")

        label = f"Ingest + score ({len(sources)} source{'s' if len(sources) != 1 else ''})"

        def task(ctx: JobContext) -> dict:
            taste = store.get_taste_profile() if use_taste else ""
            items: list[LibraryItem] = []
            for i, src in enumerate(sources, 1):
                ctx.progress((i - 1) / (len(sources) * 2), f"Ingesting {src[:80]}…")
                kind = "YouTube" if is_youtube_url(src) else "local file"
                ctx.info(f"[{i}/{len(sources)}] {kind}: {src[:80]}")
                result = ingest_source(src, workdir, transcribe_if_missing=True)
                marker = "(auto-transcribed)" if result.transcribed else "(captions found)"
                ctx.info(f"  → {result.item.video_path.name} {marker}")
                items.append(result.item)

            # Second half: scoring
            ctx.info(f"Scoring {len(items)} video(s) with {model}…")
            for j, item in enumerate(items):
                base = 0.5 + (j / len(items)) * 0.5
                span = (1 / len(items)) * 0.5
                _score_one(
                    store, item,
                    model=model, max_clips=max_clips, min_score=min_score,
                    taste=taste, ctx=ctx, base=base, span=span,
                )
            return {"count": len(items)}

        job = job_manager.submit("ingest", label, task)
        return {"job_id": job.id}

    @app.post("/api/rescore")
    async def rescore_api(
        video_id: int = Form(...),
        model: str = Form("claude-sonnet-4-6"),
        max_clips: int = Form(10),
        min_score: int = Form(70),
        use_taste: bool = Form(True),
    ):
        videos = {v.id: v for v in store.list_videos()}
        if video_id not in videos:
            raise HTTPException(404, "Video not found")
        v = videos[video_id]
        item = LibraryItem(video_path=Path(v.video_path), transcript_path=Path(v.transcript_path))

        def task(ctx: JobContext) -> dict:
            taste = store.get_taste_profile() if use_taste else ""
            ctx.info(f"Re-scoring {v.slug} with taste profile={'yes' if taste else 'no'}")
            _score_one(
                store, item,
                model=model, max_clips=max_clips, min_score=min_score,
                taste=taste, ctx=ctx, base=0.0, span=1.0,
            )
            return {"video": v.slug}

        job = job_manager.submit("rescore", f"Re-score: {v.slug}", task)
        return {"job_id": job.id}

    @app.post("/api/distill-taste")
    async def distill_api():
        def task(ctx: JobContext) -> dict:
            ctx.info("Reading feedback history…", progress=0.2)
            profile = distill_taste_profile(store)
            ctx.info("Taste profile updated.", progress=1.0)
            return {"profile": profile}
        job = job_manager.submit("distill", "Distill taste profile", task)
        return {"job_id": job.id}

    @app.get("/api/jobs")
    async def jobs_list():
        return [
            {
                "id": j.id, "kind": j.kind, "label": j.label, "status": j.status.value,
                "progress": j.progress, "created_at": j.created_at, "error": j.error,
            }
            for j in job_manager.list()
        ]

    @app.get("/api/jobs/{job_id}/stream")
    async def jobs_stream(job_id: str):
        try:
            queue = await job_manager.subscribe(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found")

        async def event_source():
            try:
                while True:
                    line: LogLine | None = await queue.get()
                    if line is None:
                        yield "event: done\ndata: {}\n\n"
                        return
                    payload = {
                        "ts": line.ts, "level": line.level,
                        "message": line.message, "progress": line.progress,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
            finally:
                job_manager.unsubscribe(job_id, queue)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- Clip review -----------------------------------------------------------
    @app.post("/clips/{clip_id}/approve")
    def approve(clip_id: int, reason: str = Form("")):
        clip = store.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        store.update_clip(clip_id, status="approved")
        store.record_feedback(
            clip_id=clip_id, kind="approve", reason=reason,
            original={"start": clip.start_sec, "end": clip.end_sec, "title": clip.title},
        )
        return JSONResponse({"ok": True, "status": "approved"})

    @app.post("/clips/{clip_id}/reject")
    def reject(clip_id: int, reason: str = Form("")):
        clip = store.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        store.update_clip(clip_id, status="rejected")
        store.record_feedback(
            clip_id=clip_id, kind="reject", reason=reason,
            original={"start": clip.start_sec, "end": clip.end_sec, "title": clip.title},
        )
        return JSONResponse({"ok": True, "status": "rejected"})

    @app.post("/clips/{clip_id}/update")
    def update(
        clip_id: int,
        start: float = Form(...),
        end: float = Form(...),
        title: str = Form(""),
        reason: str = Form(""),
    ):
        if end <= start:
            raise HTTPException(400, "end must be greater than start")
        existing = store.get_clip(clip_id)
        if not existing:
            raise HTTPException(404, "Clip not found")
        store.update_clip(clip_id, start=start, end=end, title=title or None)
        store.record_feedback(
            clip_id=clip_id, kind="edit", reason=reason,
            original={"start": existing.start_sec, "end": existing.end_sec, "title": existing.title},
            new={"start": start, "end": end, "title": title},
        )
        return JSONResponse({"ok": True})

    @app.post("/clips/{clip_id}/note")
    def note(clip_id: int, reason: str = Form(...)):
        store.record_feedback(clip_id=clip_id, kind="note", reason=reason)
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
            vertical=vertical, burn_captions=burn_captions,
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
                vertical=vertical, burn_captions=burn_captions,
                fast_copy=not (vertical or burn_captions),
                caption_text=clip.title or clip.hook,
            )
            cut_clip(Path(clip.video_path), clip.start_sec, clip.end_sec, out, opts)
            store.update_clip(clip.id, status="rendered", output_path=str(out))
            rendered.append(str(out))
        return JSONResponse({"ok": True, "rendered": rendered, "count": len(rendered)})

    @app.post("/api/preferences")
    def set_prefs(taste: str = Form(...)):
        store.set_taste_profile(taste)
        return {"ok": True}

    # --- Media streaming -------------------------------------------------------
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

    app = create_app(db_path, output_dir, workdir=Path(db_path).parent)
    uvicorn.run(app, host=host, port=port)
