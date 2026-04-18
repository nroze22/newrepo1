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
