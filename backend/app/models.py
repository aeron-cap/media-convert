from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

Format = Literal["mp3", "m4a", "ogg", "flac", "wav", "mp4", "webm", "mkv"]
Status = Literal["queued", "running", "done", "failed"]


class ProbeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class VideoInfo(BaseModel):
    kind: Literal["video"] = "video"
    video_id: str | None
    title: str | None
    thumbnail: str | None
    duration: int | None
    uploader: str | None
    # Height in pixels of the best video stream, to limit the quality choices.
    max_height: int | None


class PlaylistEntry(BaseModel):
    video_id: str
    title: str | None
    duration: int | None
    thumbnail: str | None


class PlaylistInfo(BaseModel):
    kind: Literal["playlist"] = "playlist"
    title: str | None
    uploader: str | None
    entries: list[PlaylistEntry]
    # How many videos the playlist has; more than len(entries) if it was cut
    # off at MAX_PLAYLIST_ITEMS.
    total: int
    truncated: bool


class FormatInfo(BaseModel):
    id: Format
    label: str
    kind: Literal["audio", "video"]
    qualities: list[str]
    default_quality: str | None
    note: str | None


class JobCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    format: Format
    # Audio bitrate in kbps or maximum video height; omit for the default.
    quality: str | None = Field(default=None, max_length=16)


class QualityUpdate(BaseModel):
    """A new quality for conversions that are still waiting to start."""

    quality: str = Field(min_length=1, max_length=16)


class Job(BaseModel):
    id: str
    url: str
    format: Format
    quality: str | None
    status: Status
    stage: str | None
    progress: float
    error: str | None
    video_id: str | None
    title: str | None
    thumbnail: str | None
    duration: int | None
    filesize: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    # Set for jobs created from a playlist: the batch and the place in it.
    batch_id: str | None
    position: int | None
    download_url: str | None
    # When the job and its file are deleted automatically; null if never.
    expires_at: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any], retention_hours: int) -> "Job":
        download_url = f"/api/jobs/{row['id']}/file" if row["status"] == "done" else None
        expires_at = None
        if row["finished_at"] and retention_hours > 0:
            finished = datetime.fromisoformat(row["finished_at"])
            expires_at = (finished + timedelta(hours=retention_hours)).isoformat(timespec="seconds")
        return cls(**row, download_url=download_url, expires_at=expires_at)


class BatchEntry(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str | None = Field(default=None, max_length=300)


class BatchCreate(BaseModel):
    # The playlist link, kept for reference.
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    format: Format
    quality: str | None = Field(default=None, max_length=16)
    entries: list[BatchEntry] = Field(min_length=1)


class Batch(BaseModel):
    id: str
    url: str
    title: str | None
    format: Format
    quality: str | None
    created_at: str
    zip_url: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Batch":
        return cls(**row, zip_url=f"/api/batches/{row['id']}/zip")


class BatchCreated(BaseModel):
    batch: Batch
    jobs: list[Job]
