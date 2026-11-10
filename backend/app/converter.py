"""Fetching from YouTube (yt-dlp) and converting (ffmpeg, driven by yt-dlp)."""

import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

# Output formats: file extension plus the yt-dlp options that produce it.
FORMATS: dict[str, dict[str, Any]] = {
    "mp3": {
        "ext": "mp3",
        "opts": {
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        },
    },
    "mp4": {
        "ext": "mp4",
        "opts": {
            "format": "bv*+ba/b",
            # Prefer H.264 + AAC so the MP4 plays everywhere without re-encoding.
            "format_sort": ["vcodec:h264", "res", "acodec:m4a"],
            "merge_output_format": "mp4",
        },
    },
}

# (stage, fraction 0..1) -> None
ProgressCallback = Callable[[str, float], None]
# Receives video metadata before the download starts; may raise ConversionError.
InfoCallback = Callable[[dict[str, Any]], None]


class ConversionError(Exception):
    """A failure with a message that is safe and useful to show to the user."""


def validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ConversionError("Enter a YouTube link (youtube.com or youtu.be).")
    return url


def safe_filename(title: str | None, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150].rstrip(" .") or fallback


def _base_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
    }


def _friendly(error: DownloadError) -> ConversionError:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    message = re.sub(r"^ERROR:\s*", "", message)
    message = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", message)
    return ConversionError(message.strip() or "The video could not be fetched.")


def _summary(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") == "playlist":
        raise ConversionError("Playlists aren't supported yet. Use a link to a single video.")
    if info.get("is_live"):
        raise ConversionError("Live streams can't be converted while they are live.")
    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": int(info["duration"]) if info.get("duration") else None,
        "uploader": info.get("uploader") or info.get("channel"),
    }


def probe(url: str) -> dict[str, Any]:
    """Return metadata for a single video without downloading it."""
    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as error:
        raise _friendly(error) from error
    return _summary(info)


def convert(
    url: str,
    format: str,
    out_dir: Path,
    job_id: str,
    on_info: InfoCallback,
    on_progress: ProgressCallback,
) -> Path:
    """Download `url` and convert it to `format`; return the finished file."""
    url = validate_url(url)
    spec = FORMATS[format]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{job_id}.{spec['ext']}"

    def download_hook(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading":
            return
        on_progress("downloading", _download_fraction(d))

    def postprocess_hook(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            on_progress("converting", 1.0)

    opts = {
        **_base_opts(),
        **spec["opts"],
        "outtmpl": str(out_dir / f"{job_id}.%(ext)s"),
        "progress_hooks": [download_hook],
        "postprocessor_hooks": [postprocess_hook],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            on_info(_summary(info))
            ydl.process_ie_result(info, download=True)
    except DownloadError as error:
        _remove_leftovers(out_dir, job_id)
        raise _friendly(error) from error
    except BaseException:
        _remove_leftovers(out_dir, job_id)
        raise

    if not target.is_file():
        _remove_leftovers(out_dir, job_id)
        raise ConversionError("The conversion finished without producing a file.")
    _remove_leftovers(out_dir, job_id, keep=target)
    return target


def _download_fraction(d: dict[str, Any]) -> float:
    """Overall download progress, across the separate video and audio streams."""
    downloaded = d.get("downloaded_bytes") or 0
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    info = d.get("info_dict") or {}
    parts = info.get("requested_formats") or []
    sizes = [p.get("filesize") or p.get("filesize_approx") for p in parts]

    if len(parts) > 1 and all(sizes):
        # Streams download one after another; add up the ones already finished.
        current = info.get("format_id")
        done = 0
        for part, size in zip(parts, sizes):
            if part.get("format_id") == current:
                break
            done += size
        return min((done + downloaded) / sum(sizes), 1.0)

    return min(downloaded / total, 1.0) if total else 0.0


def _remove_leftovers(out_dir: Path, job_id: str, keep: Path | None = None) -> None:
    for path in out_dir.glob(f"{job_id}.*"):
        if path != keep:
            path.unlink(missing_ok=True)
