from typing import Any, Literal

from pydantic import BaseModel, Field

Format = Literal["mp3", "m4a", "ogg", "flac", "wav", "mp4", "webm", "mkv"]
Status = Literal["queued", "running", "done", "failed"]


class ProbeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class VideoInfo(BaseModel):
    video_id: str | None
    title: str | None
    thumbnail: str | None
    duration: int | None
    uploader: str | None
    # Height in pixels of the best video stream, to limit the quality choices.
    max_height: int | None


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
    download_url: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        download_url = f"/api/jobs/{row['id']}/file" if row["status"] == "done" else None
        return cls(**row, download_url=download_url)
