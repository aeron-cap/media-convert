import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    format      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    stage       TEXT,
    progress    REAL NOT NULL DEFAULT 0,
    error       TEXT,
    video_id    TEXT,
    title       TEXT,
    thumbnail   TEXT,
    duration    INTEGER,
    filename    TEXT,
    filesize    INTEGER,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_created_at ON jobs (created_at);
"""

# Columns update_job() may write. Anything else is a programming error.
_UPDATABLE = {
    "status", "stage", "progress", "error", "video_id", "title", "thumbnail",
    "duration", "filename", "filesize", "started_at", "finished_at",
}

UNFINISHED = ("queued", "running")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def create_job(self, url: str, format: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, url, format, created_at) VALUES (?, ?, ?, ?)",
                (job_id, url, format, now()),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_job(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id)
            )

    def fail_unfinished(self, error: str) -> int:
        """Mark jobs left queued or running (e.g. by a restart) as failed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = 'failed', stage = NULL, error = ?, finished_at = ? "
                "WHERE status IN (?, ?)",
                (error, now(), *UNFINISHED),
            )
            return cursor.rowcount
