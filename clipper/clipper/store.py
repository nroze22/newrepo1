"""SQLite storage for videos, candidate clips, and review decisions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .scorer import ClipCandidate

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    video_path TEXT NOT NULL,
    transcript_path TEXT NOT NULL,
    duration REAL,
    source_url TEXT,
    title TEXT,
    channel TEXT,
    scanned_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    title TEXT,
    hook TEXT,
    rationale TEXT,
    score INTEGER,
    tags TEXT,
    transcript_excerpt TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    output_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER REFERENCES clips(id) ON DELETE CASCADE,
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,            -- approve | reject | edit | note
    reason TEXT,                   -- optional freeform explanation
    original_start REAL,           -- for 'edit' feedback: original timestamps/title
    original_end REAL,
    original_title TEXT,
    new_start REAL,
    new_end REAL,
    new_title TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_clip ON feedback(clip_id);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    taste_profile TEXT DEFAULT '',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO preferences (id, taste_profile) VALUES (1, '');

CREATE TABLE IF NOT EXISTS episode_briefs (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    brief_json TEXT NOT NULL,       -- full EpisodeBrief serialized
    domain TEXT,
    coverage_note TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_packages (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    package_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_kit (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kit_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO brand_kit (id, kit_json) VALUES (1, '{}');

CREATE TABLE IF NOT EXISTS clip_embeddings (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vector BLOB NOT NULL,
    source_text TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_thumbnails (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    thumbnails_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_faithfulness (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,        -- safe | risky | unsafe
    concern TEXT,
    fix_hint TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS diarization (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    timeline_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS postiz_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    base_url TEXT DEFAULT '',
    api_key TEXT DEFAULT '',
    integrations_json TEXT DEFAULT '[]',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO postiz_config (id, base_url, api_key) VALUES (1, '', '');

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    integration_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    postiz_post_id TEXT,
    status TEXT NOT NULL,          -- scheduled | draft | published | error
    scheduled_at TEXT,
    content TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sched_clip ON scheduled_posts(clip_id);
"""


@dataclass
class VideoRow:
    id: int
    slug: str
    video_path: str
    transcript_path: str
    duration: float | None
    source_url: str | None = None
    title: str | None = None
    channel: str | None = None


@dataclass
class ClipRow:
    id: int
    video_id: int
    start_sec: float
    end_sec: float
    title: str
    hook: str
    rationale: str
    score: int
    tags: list[str]
    transcript_excerpt: str
    status: str
    output_path: str | None
    video_slug: str = ""
    video_path: str = ""


@dataclass
class FeedbackRow:
    id: int
    clip_id: int | None
    video_id: int | None
    kind: str
    reason: str
    original_start: float | None
    original_end: float | None
    original_title: str | None
    new_start: float | None
    new_end: float | None
    new_title: str | None
    created_at: str


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migrate older DBs that predate the new columns.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
            for col, ddl in (
                ("source_url", "ALTER TABLE videos ADD COLUMN source_url TEXT"),
                ("title", "ALTER TABLE videos ADD COLUMN title TEXT"),
                ("channel", "ALTER TABLE videos ADD COLUMN channel TEXT"),
            ):
                if col not in cols:
                    conn.execute(ddl)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_video(
        self,
        slug: str,
        video_path: Path,
        transcript_path: Path,
        duration: float,
        *,
        source_url: str | None = None,
        title: str | None = None,
        channel: str | None = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO videos (slug, video_path, transcript_path, duration, source_url, title, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    video_path=excluded.video_path,
                    transcript_path=excluded.transcript_path,
                    duration=excluded.duration,
                    source_url=COALESCE(excluded.source_url, videos.source_url),
                    title=COALESCE(excluded.title, videos.title),
                    channel=COALESCE(excluded.channel, videos.channel)
                """,
                (slug, str(video_path), str(transcript_path), duration, source_url, title, channel),
            )
            if cur.lastrowid:
                return cur.lastrowid
            row = conn.execute("SELECT id FROM videos WHERE slug = ?", (slug,)).fetchone()
            return int(row["id"])

    def replace_clips(self, video_id: int, clips: list[ClipCandidate]) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM clips WHERE video_id = ? AND status = 'pending'",
                (video_id,),
            )
            for c in clips:
                conn.execute(
                    """
                    INSERT INTO clips
                        (video_id, start_sec, end_sec, title, hook, rationale,
                         score, tags, transcript_excerpt, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        video_id,
                        c.start,
                        c.end,
                        c.title,
                        c.hook,
                        c.rationale,
                        c.score,
                        json.dumps(c.tags),
                        c.transcript_excerpt,
                    ),
                )

    def list_videos(self) -> list[VideoRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, slug, video_path, transcript_path, duration, "
                "source_url, title, channel FROM videos ORDER BY slug"
            ).fetchall()
        return [VideoRow(**dict(r)) for r in rows]

    def list_clips(self, *, video_id: int | None = None, status: str | None = None) -> list[ClipRow]:
        query = (
            "SELECT c.*, v.slug AS video_slug, v.video_path AS video_path "
            "FROM clips c JOIN videos v ON v.id = c.video_id"
        )
        conds: list[str] = []
        params: list = []
        if video_id is not None:
            conds.append("c.video_id = ?")
            params.append(video_id)
        if status:
            conds.append("c.status = ?")
            params.append(status)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY c.score DESC, c.id ASC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_clip(r) for r in rows]

    def get_clip(self, clip_id: int) -> ClipRow | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT c.*, v.slug AS video_slug, v.video_path AS video_path "
                "FROM clips c JOIN videos v ON v.id = c.video_id WHERE c.id = ?",
                (clip_id,),
            ).fetchone()
        return self._row_to_clip(row) if row else None

    def update_clip(
        self,
        clip_id: int,
        *,
        status: str | None = None,
        start: float | None = None,
        end: float | None = None,
        title: str | None = None,
        output_path: str | None = None,
    ) -> None:
        fields = []
        params: list = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if start is not None:
            fields.append("start_sec = ?")
            params.append(start)
        if end is not None:
            fields.append("end_sec = ?")
            params.append(end)
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if output_path is not None:
            fields.append("output_path = ?")
            params.append(output_path)
        if not fields:
            return
        params.append(clip_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE clips SET {', '.join(fields)} WHERE id = ?", params)

    def record_feedback(
        self,
        *,
        clip_id: int | None,
        kind: str,
        reason: str = "",
        original: dict | None = None,
        new: dict | None = None,
    ) -> int:
        orig = original or {}
        nw = new or {}
        video_id = None
        if clip_id is not None:
            clip = self.get_clip(clip_id)
            if clip:
                video_id = clip.video_id
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO feedback
                    (clip_id, video_id, kind, reason,
                     original_start, original_end, original_title,
                     new_start, new_end, new_title)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clip_id, video_id, kind, reason or "",
                    orig.get("start"), orig.get("end"), orig.get("title"),
                    nw.get("start"), nw.get("end"), nw.get("title"),
                ),
            )
            return int(cur.lastrowid)

    def list_feedback(self, *, limit: int = 500) -> list[FeedbackRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, clip_id, video_id, kind, reason, "
                "original_start, original_end, original_title, "
                "new_start, new_end, new_title, created_at "
                "FROM feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [FeedbackRow(**dict(r)) for r in rows]

    def feedback_stats(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM feedback GROUP BY kind"
            ).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}

    def save_brief(self, video_id: int, brief: dict, *, coverage_note: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO episode_briefs (video_id, brief_json, domain, coverage_note, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(video_id) DO UPDATE SET "
                "brief_json=excluded.brief_json, domain=excluded.domain, "
                "coverage_note=excluded.coverage_note, updated_at=CURRENT_TIMESTAMP",
                (video_id, json.dumps(brief), brief.get("domain", ""), coverage_note),
            )

    def get_brief(self, video_id: int) -> tuple[dict | None, str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT brief_json, coverage_note FROM episode_briefs WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        if not row:
            return None, ""
        try:
            return json.loads(row["brief_json"]), (row["coverage_note"] or "")
        except json.JSONDecodeError:
            return None, ""

    # ---- Postiz -------------------------------------------------------------

    def get_postiz_config(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT base_url, api_key, integrations_json FROM postiz_config WHERE id = 1"
            ).fetchone()
        if not row:
            return {"base_url": "", "api_key": "", "integrations": []}
        try:
            integrations = json.loads(row["integrations_json"] or "[]")
        except json.JSONDecodeError:
            integrations = []
        return {
            "base_url": row["base_url"] or "",
            "api_key": row["api_key"] or "",
            "integrations": integrations,
        }

    def save_postiz_config(self, *, base_url: str, api_key: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO postiz_config (id, base_url, api_key, updated_at) "
                "VALUES (1, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET "
                "base_url=excluded.base_url, api_key=excluded.api_key, "
                "updated_at=CURRENT_TIMESTAMP",
                (base_url or "", api_key or ""),
            )

    def save_postiz_integrations(self, integrations: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE postiz_config SET integrations_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = 1",
                (json.dumps(integrations or []),),
            )

    def record_scheduled_post(
        self,
        *,
        clip_id: int,
        integration_id: str,
        provider: str,
        postiz_post_id: str | None,
        status: str,
        scheduled_at: str | None,
        content: str = "",
        error: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO scheduled_posts "
                "(clip_id, integration_id, provider, postiz_post_id, status, scheduled_at, content, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (clip_id, integration_id, provider, postiz_post_id, status, scheduled_at, content, error),
            )
            return int(cur.lastrowid)

    def list_scheduled_posts(self, *, clip_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM scheduled_posts"
        params: list = []
        if clip_id is not None:
            query += " WHERE clip_id = ?"
            params.append(clip_id)
        query += " ORDER BY id DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def delete_scheduled_post(self, id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (id,))

    def save_brand_kit(self, kit: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO brand_kit (id, kit_json, updated_at) VALUES (1, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET kit_json=excluded.kit_json, updated_at=CURRENT_TIMESTAMP",
                (json.dumps(kit or {}),),
            )

    def get_brand_kit(self) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT kit_json FROM brand_kit WHERE id = 1").fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["kit_json"]) or {}
        except json.JSONDecodeError:
            return {}

    def save_embedding(self, clip_id: int, *, model: str, vector: bytes, source_text: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO clip_embeddings (clip_id, model, vector, source_text, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(clip_id) DO UPDATE SET model=excluded.model, "
                "vector=excluded.vector, source_text=excluded.source_text, "
                "updated_at=CURRENT_TIMESTAMP",
                (clip_id, model, vector, source_text[:1000]),
            )

    def list_embeddings(self) -> list[tuple[int, int, bytes]]:
        """Return (clip_id, video_id, vector_bytes) for every embedded clip."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT e.clip_id, c.video_id, e.vector FROM clip_embeddings e "
                "JOIN clips c ON c.id = e.clip_id"
            ).fetchall()
        return [(int(r["clip_id"]), int(r["video_id"]), bytes(r["vector"])) for r in rows]

    def save_thumbnails(self, clip_id: int, thumbnails: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO clip_thumbnails (clip_id, thumbnails_json, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(clip_id) DO UPDATE SET thumbnails_json=excluded.thumbnails_json, "
                "updated_at=CURRENT_TIMESTAMP",
                (clip_id, json.dumps(thumbnails or {})),
            )

    def get_thumbnails(self, clip_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT thumbnails_json FROM clip_thumbnails WHERE clip_id = ?", (clip_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["thumbnails_json"])
        except json.JSONDecodeError:
            return None

    def save_faithfulness(self, clip_id: int, verdict: str, concern: str = "", fix_hint: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO clip_faithfulness (clip_id, verdict, concern, fix_hint, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(clip_id) DO UPDATE SET verdict=excluded.verdict, "
                "concern=excluded.concern, fix_hint=excluded.fix_hint, "
                "updated_at=CURRENT_TIMESTAMP",
                (clip_id, verdict, concern, fix_hint),
            )

    def get_faithfulness(self, clip_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT verdict, concern, fix_hint FROM clip_faithfulness WHERE clip_id = ?",
                (clip_id,),
            ).fetchone()
        if not row:
            return None
        return {"verdict": row["verdict"], "concern": row["concern"] or "", "fix_hint": row["fix_hint"] or ""}

    def save_diarization(self, video_id: int, timeline: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO diarization (video_id, timeline_json, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(video_id) DO UPDATE SET timeline_json=excluded.timeline_json, "
                "updated_at=CURRENT_TIMESTAMP",
                (video_id, json.dumps(timeline or {})),
            )

    def get_diarization(self, video_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT timeline_json FROM diarization WHERE video_id = ?", (video_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["timeline_json"])
        except json.JSONDecodeError:
            return None

    def save_clip_package(self, clip_id: int, package: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO clip_packages (clip_id, package_json, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(clip_id) DO UPDATE SET "
                "package_json=excluded.package_json, updated_at=CURRENT_TIMESTAMP",
                (clip_id, json.dumps(package)),
            )

    def get_clip_package(self, clip_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT package_json FROM clip_packages WHERE clip_id = ?", (clip_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["package_json"])
        except json.JSONDecodeError:
            return None

    def get_taste_profile(self) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT taste_profile FROM preferences WHERE id = 1"
            ).fetchone()
        return (row["taste_profile"] if row else "") or ""

    def set_taste_profile(self, profile: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO preferences (id, taste_profile, updated_at) "
                "VALUES (1, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET taste_profile=excluded.taste_profile, "
                "updated_at=CURRENT_TIMESTAMP",
                (profile or "",),
            )

    @staticmethod
    def _row_to_clip(row: sqlite3.Row) -> ClipRow:
        tags = row["tags"]
        try:
            parsed = json.loads(tags) if tags else []
        except json.JSONDecodeError:
            parsed = []
        return ClipRow(
            id=int(row["id"]),
            video_id=int(row["video_id"]),
            start_sec=float(row["start_sec"]),
            end_sec=float(row["end_sec"]),
            title=row["title"] or "",
            hook=row["hook"] or "",
            rationale=row["rationale"] or "",
            score=int(row["score"] or 0),
            tags=parsed,
            transcript_excerpt=row["transcript_excerpt"] or "",
            status=row["status"],
            output_path=row["output_path"],
            video_slug=row["video_slug"] if "video_slug" in row.keys() else "",
            video_path=row["video_path"] if "video_path" in row.keys() else "",
        )
