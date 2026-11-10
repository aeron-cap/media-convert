import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse

from .. import converter
from ..config import get_settings
from ..converter import ConversionError
from ..db import UNFINISHED, Database
from ..models import FormatInfo, Job, JobCreate, ProbeRequest, VideoInfo
from ..worker import Worker

router = APIRouter()

# Seconds between database checks while streaming a job's progress, and how
# long the stream may stay silent before a keep-alive comment is sent.
EVENTS_POLL = 0.5
EVENTS_KEEPALIVE = 15.0


def _db(request: Request) -> Database:
    return request.app.state.db


def _worker(request: Request) -> Worker:
    return request.app.state.worker


def _get_or_404(request: Request, job_id: str) -> dict:
    row = _db(request).get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return row


# Plain `def` endpoints run in FastAPI's thread pool, so the blocking
# yt-dlp and SQLite calls below don't stall the event loop.


@router.get("/formats", response_model=list[FormatInfo])
def list_formats() -> list[dict]:
    return [
        {
            "id": format_id,
            "label": spec["label"],
            "kind": spec["kind"],
            "qualities": spec["qualities"],
            "default_quality": converter.default_quality(format_id),
            "note": spec.get("note"),
        }
        for format_id, spec in converter.FORMATS.items()
    ]


@router.post("/probe", response_model=VideoInfo)
def probe(body: ProbeRequest) -> dict:
    try:
        return converter.probe(body.url)
    except ConversionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/jobs", response_model=Job, status_code=201)
def create_job(body: JobCreate, request: Request) -> Job:
    try:
        url = converter.validate_url(body.url)
        quality = converter.normalize_quality(body.format, body.quality)
    except ConversionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    row = _db(request).create_job(url, body.format, quality)
    _worker(request).submit(row["id"])
    return Job.from_row(row)


@router.get("/jobs", response_model=list[Job])
def list_jobs(request: Request) -> list[Job]:
    return [Job.from_row(row) for row in _db(request).list_jobs()]


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, request: Request) -> Job:
    return Job.from_row(_get_or_404(request, job_id))


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: the job as JSON each time it changes, until it ends."""
    db = _db(request)
    if await run_in_threadpool(db.get_job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def stream() -> AsyncIterator[str]:
        previous = None
        idle = 0.0
        while not await request.is_disconnected():
            row = await run_in_threadpool(db.get_job, job_id)
            if row is None:
                return
            payload = Job.from_row(row).model_dump_json()
            if payload != previous:
                previous, idle = payload, 0.0
                yield f"data: {payload}\n\n"
            elif idle >= EVENTS_KEEPALIVE:
                idle = 0.0
                yield ": keep-alive\n\n"
            if row["status"] not in UNFINISHED:
                return
            await asyncio.sleep(EVENTS_POLL)
            idle += EVENTS_POLL

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/file")
def download_file(job_id: str, request: Request) -> FileResponse:
    row = _get_or_404(request, job_id)
    if row["status"] != "done" or not row["filename"]:
        raise HTTPException(status_code=409, detail="This job has no file yet.")
    path = get_settings().files_dir / row["filename"]
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The file is no longer available.")
    name = converter.safe_filename(row["title"], fallback=row["video_id"] or job_id)
    return FileResponse(path, filename=f"{name}{path.suffix}")
