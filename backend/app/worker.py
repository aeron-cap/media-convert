import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import converter
from .config import Settings
from .converter import ConversionError
from .db import Database, now

log = logging.getLogger(__name__)

# Minimum seconds between progress writes to the database for one job.
PROGRESS_INTERVAL = 0.5


class JobCancelled(Exception):
    """Raised inside a running conversion to stop it."""


class Worker:
    """Runs conversion jobs on a small thread pool, recording state in SQLite."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._cancelled: set[str] = set()

    def start(self) -> None:
        interrupted = self.db.fail_unfinished("Interrupted by a server restart.")
        if interrupted:
            log.warning("marked %d interrupted job(s) as failed", interrupted)
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.max_concurrent_jobs, thread_name_prefix="convert"
        )

    def stop(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def submit(self, job_id: str) -> None:
        if self._executor is None:
            raise RuntimeError("worker is not started")
        self._executor.submit(self._run, job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a running job to stop and discard its files.

        Returns False when the job isn't running; the caller then owns the
        clean-up. A queued job needs no cancelling: deleting its row is enough.
        """
        with self._lock:
            if job_id not in self._running:
                return False
            self._cancelled.add(job_id)
            return True

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _run(self, job_id: str) -> None:
        with self._lock:
            self._running.add(job_id)
        try:
            self._convert(job_id)
        finally:
            with self._lock:
                self._running.discard(job_id)
                cancelled = job_id in self._cancelled
                self._cancelled.discard(job_id)
            if cancelled:
                converter.remove_files(self.settings.files_dir, job_id)

    def _convert(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if job is None or job["status"] != "queued":
            return
        self.db.update_job(job_id, status="running", stage="starting", started_at=now())

        last_write = 0.0
        last_stage = ""

        def on_info(info: dict[str, Any]) -> None:
            if self._is_cancelled(job_id):
                raise JobCancelled
            limit = self.settings.max_duration_minutes
            if limit and (info["duration"] or 0) > limit * 60:
                raise ConversionError(f"This video is longer than the {limit}-minute limit.")
            self.db.update_job(
                job_id,
                video_id=info["video_id"],
                title=info["title"],
                thumbnail=info["thumbnail"],
                duration=info["duration"],
            )

        def on_progress(stage: str, fraction: float) -> None:
            nonlocal last_write, last_stage
            if self._is_cancelled(job_id):
                raise JobCancelled
            current = time.monotonic()
            if stage == last_stage and current - last_write < PROGRESS_INTERVAL:
                return
            last_write, last_stage = current, stage
            self.db.update_job(job_id, stage=stage, progress=round(fraction, 4))

        try:
            path = converter.convert(
                job["url"],
                job["format"],
                job["quality"],
                self.settings.files_dir,
                job_id,
                on_info,
                on_progress,
            )
        except JobCancelled:
            log.info("job %s cancelled", job_id)
        except ConversionError as error:
            self._fail(job_id, str(error))
        except Exception:
            if self._is_cancelled(job_id):
                # yt-dlp may wrap the cancellation in an error of its own.
                log.info("job %s cancelled", job_id)
                return
            log.exception("job %s failed unexpectedly", job_id)
            self._fail(job_id, "Something went wrong during the conversion.")
        else:
            self.db.update_job(
                job_id,
                status="done",
                stage=None,
                progress=1.0,
                filename=path.name,
                filesize=path.stat().st_size,
                finished_at=now(),
            )

    def _fail(self, job_id: str, error: str) -> None:
        self.db.update_job(job_id, status="failed", stage=None, error=error, finished_at=now())
