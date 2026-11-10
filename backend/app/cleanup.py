"""Deleting old conversions so the data directory doesn't grow forever."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi.concurrency import run_in_threadpool

from . import converter
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

# Seconds between clean-up runs.
CLEANUP_INTERVAL = 600
# A file with no job is only removed once it is this old, so files of a
# conversion that is still being cancelled are left alone.
ORPHAN_AGE = 3600


def run_cleanup(db: Database, settings: Settings) -> dict[str, int]:
    """Remove expired jobs with their files, and files that belong to no job."""
    expired = 0
    if settings.retention_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.retention_hours)
        for job in db.finished_before(cutoff.isoformat(timespec="seconds")):
            db.delete_job(job["id"])
            converter.remove_files(settings.files_dir, job["id"])
            expired += 1

    orphans = 0
    if settings.files_dir.is_dir():
        known = db.job_ids()
        oldest_kept = time.time() - ORPHAN_AGE
        for path in settings.files_dir.iterdir():
            job_id = path.name.split(".", 1)[0]
            if path.is_file() and job_id not in known and path.stat().st_mtime < oldest_kept:
                path.unlink(missing_ok=True)
                orphans += 1

    if expired or orphans:
        log.info("clean-up removed %d expired job(s) and %d stray file(s)", expired, orphans)
    return {"expired": expired, "orphans": orphans}


async def cleanup_loop(db: Database, settings: Settings) -> None:
    while True:
        try:
            await run_in_threadpool(run_cleanup, db, settings)
        except Exception:
            log.exception("clean-up failed")
        await asyncio.sleep(CLEANUP_INTERVAL)
