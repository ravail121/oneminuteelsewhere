from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import StoryPackage

SCHEMA = """
CREATE TABLE IF NOT EXISTS content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    format TEXT NOT NULL,
    title TEXT NOT NULL,
    premise TEXT NOT NULL,
    premise_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    job_dir TEXT,
    video_path TEXT,
    youtube_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_status ON content(status);
"""


class ContentStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def prior_ideas(self, limit: int = 100) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT title, premise FROM content ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [f"{row['title']} {row['premise']}" for row in rows]

    def create(self, story: StoryPackage, job_dir: Path) -> int:
        now = datetime.now(UTC).isoformat()
        digest = hashlib.sha256(story.premise.lower().encode()).hexdigest()
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO content
                (format,title,premise,premise_hash,status,job_dir,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (story.format, story.title, story.premise, digest, "generated", str(job_dir), now, now),
            )
            return int(cursor.lastrowid)

    def update(self, content_id: int, status: str, **fields: str) -> None:
        allowed = {"job_dir", "video_path", "youtube_id", "error"}
        pairs = [(key, value) for key, value in fields.items() if key in allowed]
        pairs.extend([("status", status), ("updated_at", datetime.now(UTC).isoformat())])
        sql = "UPDATE content SET " + ", ".join(f"{key}=?" for key, _ in pairs) + " WHERE id=?"
        values = [value for _, value in pairs] + [content_id]
        with self.connect() as db:
            db.execute(sql, values)

