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
"""


@dataclass
class VideoRow:
    id: int
    slug: str
    video_path: str
    transcript_path: str
    duration: float | None


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

    def upsert_video(self, slug: str, video_path: Path, transcript_path: Path, duration: float) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO videos (slug, video_path, transcript_path, duration)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    video_path=excluded.video_path,
                    transcript_path=excluded.transcript_path,
                    duration=excluded.duration
                """,
                (slug, str(video_path), str(transcript_path), duration),
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
                "SELECT id, slug, video_path, transcript_path, duration FROM videos ORDER BY slug"
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
