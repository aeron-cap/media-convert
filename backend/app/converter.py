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

AUDIO_BITRATES = ["128", "192", "256", "320"]
DEFAULT_BITRATE = "192"
# Video quality is a maximum height in pixels, or "best" for no limit.
VIDEO_QUALITIES = ["best", "1080", "720", "480", "360"]
DEFAULT_VIDEO_QUALITY = "best"

# Output formats, in the order the UI shows them.
#   kind       "audio" or "video"
#   qualities  the values `quality` may take; empty means there is no choice
#   note       shown in the UI next to formats that behave unexpectedly
FORMATS: dict[str, dict[str, Any]] = {
    "mp3": {"label": "MP3", "kind": "audio", "qualities": AUDIO_BITRATES},
    "m4a": {
        "label": "M4A",
        "kind": "audio",
        "qualities": [],
        "note": "Keeps YouTube's original AAC audio without re-encoding.",
    },
    "ogg": {"label": "OGG", "kind": "audio", "qualities": AUDIO_BITRATES},
    "flac": {"label": "FLAC", "kind": "audio", "qualities": [], "note": "Lossless, large files."},
    "wav": {"label": "WAV", "kind": "audio", "qualities": [], "note": "Uncompressed, very large files."},
    "mp4": {"label": "MP4", "kind": "video", "qualities": VIDEO_QUALITIES},
    "webm": {
        "label": "WEBM",
        "kind": "video",
        "qualities": VIDEO_QUALITIES,
        "note": "Slow for videos YouTube doesn't already offer as WEBM.",
    },
    "mkv": {"label": "MKV", "kind": "video", "qualities": VIDEO_QUALITIES},
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


def default_quality(format: str) -> str | None:
    qualities = FORMATS[format]["qualities"]
    if not qualities:
        return None
    return DEFAULT_BITRATE if FORMATS[format]["kind"] == "audio" else DEFAULT_VIDEO_QUALITY


def normalize_quality(format: str, quality: str | None) -> str | None:
    """Return the quality to use for `format`, or raise if it isn't allowed."""
    if format not in FORMATS:
        raise ConversionError(f"Unknown format: {format}.")
    qualities = FORMATS[format]["qualities"]
    if not qualities:
        return None
    if quality is None:
        return default_quality(format)
    if quality not in qualities:
        raise ConversionError(
            f"{FORMATS[format]['label']} doesn't support quality {quality!r}. "
            f"Choose one of: {', '.join(qualities)}."
        )
    return quality


def format_options(format: str, quality: str | None) -> dict[str, Any]:
    """The yt-dlp options that produce `format` at `quality`."""
    quality = normalize_quality(format, quality)

    if FORMATS[format]["kind"] == "audio":
        extract: dict[str, Any] = {"key": "FFmpegExtractAudio"}
        if format == "m4a":
            # An AAC source is copied into the M4A container untouched.
            return {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "postprocessors": [{**extract, "preferredcodec": "m4a"}],
            }
        extract["preferredcodec"] = "vorbis" if format == "ogg" else format
        if quality:
            extract["preferredquality"] = quality
        return {"format": "bestaudio/best", "postprocessors": [extract]}

    # Video. Resolution always ranks first; codecs only break ties, so each
    # container gets streams it can hold without re-encoding where they exist.
    sort = [] if quality == "best" else [f"res:{quality}"]
    if format == "mp4":
        # H.264 + AAC plays everywhere. Other codecs (above 1080p) are still
        # copied as they are rather than re-encoded.
        return {
            "format": "bv*+ba/b",
            "format_sort": [*sort, "vcodec:h264", "acodec:m4a"],
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }
    if format == "webm":
        # VP9 + Opus streams merge straight into WEBM. Anything else is
        # re-encoded by the convertor, which does nothing for a WEBM input.
        return {
            "format": "bv*+ba/b",
            "format_sort": [*sort, "vcodec:vp9", "acodec:opus"],
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "webm"}],
        }
    # MKV holds any codec, so take the best streams as they are.
    return {
        "format": "bv*+ba/b",
        "format_sort": sort,
        "merge_output_format": "mkv",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}],
    }


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
    if "confirm you" in message and "not a bot" in message:
        # yt-dlp's own text is advice for its command line, not for this app.
        return ConversionError(
            "YouTube asked this server to prove it isn't a bot. "
            "This is usually temporary; wait a minute and try again."
        )
    message = re.sub(r"^ERROR:\s*", "", message)
    message = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", message)
    return ConversionError(message.strip() or "The video could not be fetched.")


def _max_height(info: dict[str, Any]) -> int | None:
    heights = [
        f["height"]
        for f in info.get("formats") or []
        if f.get("height") and f.get("vcodec") not in (None, "none")
    ]
    return max(heights) if heights else None


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
        "max_height": _max_height(info),
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
    quality: str | None,
    out_dir: Path,
    job_id: str,
    on_info: InfoCallback,
    on_progress: ProgressCallback,
) -> Path:
    """Download `url` and convert it to `format`; return the finished file."""
    url = validate_url(url)
    format_opts = format_options(format, quality)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{job_id}.{format}"

    # (format id, size in bytes) of each stream to download, filled in once
    # the video's metadata is known. Video and audio usually come separately.
    streams: list[tuple[str, int]] = []

    def download_hook(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading":
            return
        on_progress("downloading", _download_fraction(d, streams))

    def postprocess_hook(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            on_progress("converting", 1.0)

    opts = {
        **_base_opts(),
        **format_opts,
        "outtmpl": str(out_dir / f"{job_id}.%(ext)s"),
        "progress_hooks": [download_hook],
        "postprocessor_hooks": [postprocess_hook],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            on_info(_summary(info))
            streams.extend(_stream_sizes(info))
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


def _stream_sizes(info: dict[str, Any]) -> list[tuple[str, int]]:
    """Sizes of the separate streams yt-dlp will download, if all are known."""
    parts = info.get("requested_formats") or []
    sizes = [p.get("filesize") or p.get("filesize_approx") for p in parts]
    if len(parts) < 2 or not all(sizes):
        return []
    return [(str(p.get("format_id")), int(size)) for p, size in zip(parts, sizes)]


def _download_fraction(d: dict[str, Any], streams: list[tuple[str, int]]) -> float:
    """Overall download progress, across the separate video and audio streams."""
    downloaded = d.get("downloaded_bytes") or 0
    current = str((d.get("info_dict") or {}).get("format_id"))

    if any(format_id == current for format_id, _ in streams):
        # Streams download one after another; add up the ones already finished.
        done = 0
        for format_id, size in streams:
            if format_id == current:
                break
            done += size
        return min((done + downloaded) / sum(size for _, size in streams), 1.0)

    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    return min(downloaded / total, 1.0) if total else 0.0


def _remove_leftovers(out_dir: Path, job_id: str, keep: Path | None = None) -> None:
    for path in out_dir.glob(f"{job_id}.*"):
        if path != keep:
            path.unlink(missing_ok=True)
