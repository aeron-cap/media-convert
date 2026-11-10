"""Playlists: a batch is a set of jobs created together from one playlist."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .. import converter
from ..config import get_settings
from ..converter import ConversionError
from ..models import Batch, BatchCreate, BatchCreated, Job
from ..zipstream import unique_names, zip_stream
from .jobs import _db, _remove, _worker

router = APIRouter()


def _get_or_404(request: Request, batch_id: str) -> dict:
    row = _db(request).get_batch(batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Playlist conversion not found.")
    return row


@router.post("/batches", response_model=BatchCreated, status_code=201)
def create_batch(body: BatchCreate, request: Request) -> dict:
    settings = get_settings()
    if len(body.entries) > settings.max_playlist_items:
        raise HTTPException(
            status_code=422,
            detail=f"Choose at most {settings.max_playlist_items} videos at a time.",
        )
    try:
        url = converter.validate_url(body.url)
        quality = converter.normalize_quality(body.format, body.quality)
    except ConversionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    db = _db(request)
    batch = db.create_batch(url, body.title, body.format, quality)
    jobs = [
        db.create_job(
            converter.video_url(entry.video_id),
            body.format,
            quality,
            batch_id=batch["id"],
            position=position,
            title=entry.title,
            video_id=entry.video_id,
        )
        for position, entry in enumerate(body.entries, start=1)
    ]
    for job in jobs:
        _worker(request).submit(job["id"])
    return {
        "batch": Batch.from_row(batch),
        "jobs": [Job.from_row(job, settings.retention_hours) for job in jobs],
    }


@router.get("/batches", response_model=list[Batch])
def list_batches(request: Request) -> list[Batch]:
    return [Batch.from_row(row) for row in _db(request).list_batches()]


@router.delete("/batches/{batch_id}", status_code=204)
def delete_batch(batch_id: str, request: Request) -> Response:
    """Delete a playlist conversion with all its jobs and files."""
    _get_or_404(request, batch_id)
    db = _db(request)
    for job in db.batch_jobs(batch_id):
        _remove(request, job["id"])
    db.delete_batch(batch_id)
    return Response(status_code=204)


@router.get("/batches/{batch_id}/zip")
def download_zip(batch_id: str, request: Request) -> StreamingResponse:
    """Every finished file of the playlist conversion, as one zip archive."""
    batch = _get_or_404(request, batch_id)
    files_dir = get_settings().files_dir
    paths, names = [], []
    for job in _db(request).batch_jobs(batch_id):
        path = files_dir / (job["filename"] or "")
        if job["status"] != "done" or not path.is_file():
            continue
        title = converter.safe_filename(job["title"], fallback=job["video_id"] or job["id"])
        paths.append(path)
        names.append(f"{job['position']:02d} - {title}{path.suffix}")
    if not paths:
        raise HTTPException(status_code=409, detail="No finished files to download yet.")

    archive = converter.safe_filename(batch["title"], fallback="playlist")
    return StreamingResponse(
        zip_stream(list(zip(unique_names(names), paths))),
        media_type="application/zip",
        headers={"Content-Disposition": converter.content_disposition(f"{archive}.zip")},
    )
