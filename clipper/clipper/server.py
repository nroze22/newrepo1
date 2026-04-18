"""FastAPI review UI: add sources, watch progress, review, give feedback, render."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .agents import EpisodeBrief
from .captions import BrandKit, CaptionStyle
from .cost import estimate as estimate_cost
from .cutter import CutOptions, cut_clip, render_clip
from .export import export_clip, zip_bundle
from .ingester import SourceMeta, ingest_source, is_youtube_url
from .jobs import JobContext, LogLine, manager as job_manager
from .library import LibraryItem
from .preferences import distill_taste_profile
from .produce import produce_clips
from .scorer import ClipCandidate
from . import search as search_mod
from .store import ClipRow, Store
from .thumbnails import generate_thumbnails
from .transcript import format_timestamp, parse_transcript

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.filters["ts"] = format_timestamp


def _produce_one(
    store: Store,
    item: LibraryItem,
    *,
    meta: SourceMeta | None,
    model: str,
    max_clips: int,
    min_score: int,
    taste: str,
    ctx: JobContext,
    base: float,
    span: float,
) -> int:
    """Run the full multi-agent pipeline on a single episode, persist everything."""
    transcript = parse_transcript(item.transcript_path)
    duration = transcript.duration
    video_id = store.upsert_video(
        item.slug, item.video_path, item.transcript_path, duration,
        source_url=(meta.source_url if meta else None),
        title=(meta.title if meta else None),
        channel=(meta.channel if meta else None),
    )

    def cb(pct: float, msg: str) -> None:
        ctx.progress(base + span * pct, f"{item.slug}: {msg}")

    # Scout model defaults to the chosen `model`; others keep their opus defaults
    # so the brain + editor + critic reason at the highest quality tier.
    models_override = {"scout": model}

    result = produce_clips(
        transcript, meta,
        video_slug=item.slug,
        taste_profile=taste,
        max_clips=max_clips,
        min_score=min_score,
        models=models_override,
        progress_cb=cb,
    )
    # Persist clips → packages → brief. Clips first so we have IDs.
    store.replace_clips(video_id, result.clips)
    persisted = store.list_clips(video_id=video_id)

    # Packages, aligned by order (replace_clips preserves score-descending order).
    for clip_row, package in zip(persisted, result.packages):
        store.save_clip_package(clip_row.id, package.to_dict())

    # Faithfulness verdicts
    if result.faithfulness:
        for d in result.faithfulness.decisions:
            if 1 <= d.clip_index <= len(persisted):
                clip_row = persisted[d.clip_index - 1]
                store.save_faithfulness(clip_row.id, d.verdict, d.concern, d.fix_hint)

    # Brief
    coverage = result.critic.coverage_note if result.critic else ""
    store.save_brief(video_id, result.brief.to_dict(), coverage_note=coverage)

    # Embeddings — best effort, don't fail the job if the embedding API breaks.
    try:
        texts = [
            search_mod.build_text_for_clip(
                title=c.title, hook=c.hook, excerpt=c.transcript_excerpt,
                archetype=next((t.split(":", 1)[1] for t in c.tags if t.startswith("arch:")), ""),
            )
            for c in persisted
        ]
        if texts and os.environ.get("OPENAI_API_KEY"):
            vecs = search_mod.embed_texts(texts)
            for clip_row, vec, txt in zip(persisted, vecs, texts):
                store.save_embedding(
                    clip_row.id,
                    model=search_mod.EMBEDDING_MODEL,
                    vector=vec.tobytes(), source_text=txt,
                )
            ctx.info(f"{item.slug}: embedded {len(vecs)} clip(s) for search")
    except Exception as e:
        ctx.warn(f"{item.slug}: embedding step skipped ({type(e).__name__})")

    ctx.info(
        f"{item.slug}: {len(result.clips)} clip(s) · "
        f"domain={result.brief.domain or '?'} · "
        f"archetypes={len(result.brief.archetypes)}"
    )
    if result.faithfulness:
        flagged = [d for d in result.faithfulness.decisions if d.verdict != "safe"]
        if flagged:
            ctx.info(f"{item.slug}: faithfulness flagged {len(flagged)} clip(s)")
    if result.dropped_by_critic:
        ctx.info(f"{item.slug}: critic dropped {len(result.dropped_by_critic)} clip(s)")
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
        brief_dict, coverage = store.get_brief(video_id)
        brief = EpisodeBrief.from_dict(brief_dict) if brief_dict else None
        packages = {c.id: store.get_clip_package(c.id) for c in clips}
        faithfulness = {c.id: store.get_faithfulness(c.id) for c in clips}
        thumbnails = {c.id: store.get_thumbnails(c.id) for c in clips}
        return TEMPLATES.TemplateResponse(
            request,
            "video.html",
            {
                "video": videos[video_id],
                "clips": clips,
                "brief": brief,
                "packages": packages,
                "coverage_note": coverage,
                "faithfulness": faithfulness,
                "thumbnails": thumbnails,
                "caption_styles": [
                    {"key": "tiktok_pop", "name": "TikTok Pop"},
                    {"key": "clean_minimal", "name": "Clean Minimal"},
                    {"key": "hype_shadow", "name": "Hype Shadow"},
                    {"key": "news_ticker", "name": "News Ticker"},
                ],
            },
        )

    @app.post("/api/videos/{video_id}/brief")
    def save_brief(video_id: int, brief_json: str = Form(...)):
        try:
            data = json.loads(brief_json)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON: {e}")
        store.save_brief(video_id, data)
        return {"ok": True}

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
            ingested: list[tuple[LibraryItem, SourceMeta]] = []
            for i, src in enumerate(sources, 1):
                def ingest_prog(pct: float, msg: str, _i=i) -> None:
                    ctx.progress(((_i - 1) + pct) / (len(sources) * 2), msg[:120])
                kind = "YouTube" if is_youtube_url(src) else "local file"
                ctx.info(f"[{i}/{len(sources)}] {kind}: {src[:100]}")
                result = ingest_source(src, workdir, transcribe_if_missing=True, on_progress=ingest_prog)
                caps = result.meta.captions_source or "unknown"
                ctx.info(f"  → {result.item.video_path.name} · captions: {caps}")
                ingested.append((result.item, result.meta))

            ctx.info(f"Producing {len(ingested)} episode(s) with the agent pipeline…")
            for j, (item, meta) in enumerate(ingested):
                base = 0.5 + (j / len(ingested)) * 0.5
                span = (1 / len(ingested)) * 0.5
                _produce_one(
                    store, item, meta=meta,
                    model=model, max_clips=max_clips, min_score=min_score,
                    taste=taste, ctx=ctx, base=base, span=span,
                )
            return {"count": len(ingested)}

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
            meta = SourceMeta(
                source_url=v.source_url or "",
                title=v.title or "",
                channel=v.channel or "",
            )
            ctx.info(f"Re-producing {v.slug} · taste_profile={'yes' if taste else 'no'}")
            _produce_one(
                store, item, meta=meta,
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

    def _opts_for(clip: ClipRow, *, vertical: bool, caption_style: str | None,
                  burn_captions: bool, smart_reframe: bool = True) -> CutOptions:
        kit_dict = store.get_brand_kit()
        brand_kit = BrandKit(**{k: v for k, v in kit_dict.items() if k in BrandKit.__dataclass_fields__}) if kit_dict else BrandKit()
        return CutOptions(
            vertical=vertical,
            smart_reframe=smart_reframe,
            caption_style=caption_style or None,
            burn_captions=burn_captions and not caption_style,
            caption_text=clip.title or clip.hook,
            brand_kit=brand_kit,
            fast_copy=not (vertical or burn_captions or caption_style),
        )

    def _render_one(clip: ClipRow, *, vertical: bool, caption_style: str | None,
                    burn_captions: bool, smart_reframe: bool = True) -> Path:
        out = output_dir / f"{clip.video_slug}__{clip.id:04d}.mp4"
        transcript = None
        if caption_style:
            videos = {v.id: v for v in store.list_videos()}
            video = videos.get(clip.video_id)
            if video:
                try:
                    transcript = parse_transcript(Path(video.transcript_path))
                except Exception:
                    transcript = None
        opts = _opts_for(clip, vertical=vertical, caption_style=caption_style,
                         burn_captions=burn_captions, smart_reframe=smart_reframe)
        render_clip(
            Path(clip.video_path), clip.start_sec, clip.end_sec, out,
            transcript=transcript, opts=opts,
        )
        store.update_clip(clip.id, status="rendered", output_path=str(out))
        return out

    @app.post("/clips/{clip_id}/render")
    def render(
        clip_id: int,
        vertical: bool = Form(False),
        burn_captions: bool = Form(False),
        caption_style: str = Form(""),
        smart_reframe: bool = Form(True),
    ):
        clip = store.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        out = _render_one(
            clip, vertical=vertical, caption_style=caption_style or None,
            burn_captions=burn_captions, smart_reframe=smart_reframe,
        )
        return JSONResponse({"ok": True, "output": str(out)})

    @app.post("/videos/{video_id}/render-approved")
    def render_approved(
        video_id: int,
        vertical: bool = Form(False),
        burn_captions: bool = Form(False),
        caption_style: str = Form(""),
        smart_reframe: bool = Form(True),
    ):
        approved = [c for c in store.list_clips(video_id=video_id) if c.status == "approved"]
        rendered: list[str] = []
        for clip in approved:
            out = _render_one(
                clip, vertical=vertical, caption_style=caption_style or None,
                burn_captions=burn_captions, smart_reframe=smart_reframe,
            )
            rendered.append(str(out))
        return JSONResponse({"ok": True, "rendered": rendered, "count": len(rendered)})

    @app.post("/api/preferences")
    def set_prefs(taste: str = Form(...)):
        store.set_taste_profile(taste)
        return {"ok": True}

    # --- Brand kit -------------------------------------------------------------
    @app.get("/brand", response_class=HTMLResponse)
    def brand_page(request: Request):
        kit = store.get_brand_kit() or {}
        return TEMPLATES.TemplateResponse(request, "brand.html", {"kit": kit})

    @app.post("/api/brand-kit")
    def save_brand_kit_api(
        font: str = Form("Inter"),
        primary_color: str = Form("#FFFFFF"),
        accent_color: str = Form("#FFD84D"),
        shadow_color: str = Form("#000000"),
        outline_px: int = Form(3),
        shadow_px: int = Form(2),
        uppercase: bool = Form(False),
        profanity_safe: bool = Form(False),
        logo_opacity: float = Form(0.85),
        logo_position: str = Form("top_right"),
        logo: UploadFile | None = File(None),
    ):
        kit_dir = workdir / "brand"
        kit_dir.mkdir(parents=True, exist_ok=True)
        logo_path = None
        if logo and logo.filename:
            dest = kit_dir / f"logo{Path(logo.filename).suffix.lower()}"
            with dest.open("wb") as out:
                shutil.copyfileobj(logo.file, out)
            logo_path = str(dest)
        else:
            existing = store.get_brand_kit() or {}
            logo_path = existing.get("logo_path")
        kit = {
            "font": font, "primary_color": primary_color, "accent_color": accent_color,
            "shadow_color": shadow_color, "outline_px": outline_px, "shadow_px": shadow_px,
            "uppercase": uppercase, "profanity_safe": profanity_safe,
            "logo_opacity": logo_opacity, "logo_position": logo_position,
            "logo_path": logo_path,
        }
        store.save_brand_kit(kit)
        return {"ok": True, "kit": kit}

    @app.get("/media/brand/logo")
    def brand_logo():
        kit = store.get_brand_kit()
        logo_path = (kit or {}).get("logo_path")
        if not logo_path or not Path(logo_path).exists():
            raise HTTPException(404, "No logo set")
        return FileResponse(logo_path)

    # --- Thumbnails ------------------------------------------------------------
    @app.post("/clips/{clip_id}/thumbnails")
    def generate_clip_thumbnails(clip_id: int, aspect: str = Form("16:9")):
        clip = store.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        try:
            w, h = [int(x) for x in aspect.split(":")]
        except Exception:
            w, h = 16, 9
        kit_dict = store.get_brand_kit() or {}
        kit = BrandKit(**{k: v for k, v in kit_dict.items() if k in BrandKit.__dataclass_fields__}) if kit_dict else BrandKit()
        out_dir = output_dir / "thumbnails" / f"clip_{clip_id:04d}"
        result = generate_thumbnails(
            Path(clip.video_path), clip.start_sec, clip.end_sec,
            title=clip.title, out_dir=out_dir, slug=f"clip_{clip_id:04d}",
            aspect=(w, h), brand_kit=kit,
        )
        store.save_thumbnails(clip_id, result.to_dict())
        return {"ok": True, "thumbnails": result.to_dict()}

    @app.get("/media/thumbnail/{clip_id}/{style}")
    def thumbnail_file(clip_id: int, style: str):
        data = store.get_thumbnails(clip_id) or {}
        variant = next((v for v in (data.get("variants") or []) if v.get("style") == style), None)
        if not variant or not Path(variant["path"]).exists():
            raise HTTPException(404, "Thumbnail not found — generate it first")
        return FileResponse(variant["path"], media_type="image/jpeg")

    # --- Semantic search -------------------------------------------------------
    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "search.html", {"query": "", "hits": None})

    @app.post("/api/search")
    def search_api(q: str = Form(...), top_k: int = Form(30)):
        if not q.strip():
            return {"hits": [], "query": q}
        if not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(400, "OPENAI_API_KEY not set — semantic search needs embeddings")
        rows = store.list_embeddings()
        if not rows:
            return {"hits": [], "query": q, "note": "No embeddings yet. Re-score a video to embed its clips."}
        import numpy as np
        items = [(cid, vid, search_mod._unpack(blob)) for cid, vid, blob in rows]
        qvec = search_mod.embed_one(q)
        hits = search_mod.rank(qvec, items, top_k=top_k)
        # Enrich hits with clip + video metadata.
        clip_lookup = {c.id: c for c in store.list_clips()}
        videos = {v.id: v for v in store.list_videos()}
        out = []
        for h in hits:
            clip = clip_lookup.get(h.clip_id)
            if not clip:
                continue
            v = videos.get(clip.video_id)
            out.append({
                "clip_id": h.clip_id, "video_id": h.video_id,
                "score": round(h.score, 4),
                "title": clip.title, "hook": clip.hook,
                "start_sec": clip.start_sec, "end_sec": clip.end_sec,
                "status": clip.status,
                "video_slug": v.slug if v else "",
                "video_title": (v.title if v else None) or (v.slug if v else ""),
            })
        return {"hits": out, "query": q}

    # --- Cost estimate ---------------------------------------------------------
    @app.post("/api/cost-estimate")
    def cost_api(
        urls: str = Form(""),
        duration_sec: float = Form(0.0),
        max_clips: int = Form(10),
        model: str = Form("claude-sonnet-4-6"),
    ):
        # Without downloading, estimate based on duration provided by the user.
        # (A proper "analyze this URL" would require yt-dlp metadata fetch — fast path.)
        est = estimate_cost(
            transcript=None,
            duration_sec=duration_sec or 3600,
            needs_transcription=True,
            max_clips=max_clips,
            models={"scout": model},
        )
        est_dict = est.to_dict()
        return est_dict

    # --- Export bundles --------------------------------------------------------
    @app.post("/clips/{clip_id}/export")
    def export_clip_api(clip_id: int, zip: bool = Form(True)):
        bundle_dir = output_dir / "exports"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        kit_dict = store.get_brand_kit() or {}
        kit = BrandKit(**{k: v for k, v in kit_dict.items() if k in BrandKit.__dataclass_fields__}) if kit_dict else BrandKit()
        bundle = export_clip(store, clip_id, out_dir=bundle_dir, brand_kit=kit)
        if zip:
            zip_path = zip_bundle(bundle)
            return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)
        return {"ok": True, "directory": str(bundle.directory), "files": [str(f) for f in bundle.files]}

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
