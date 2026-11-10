"""Fetching from YouTube (yt-dlp) and converting (ffmpeg, driven by yt-dlp)."""

import copy
import logging
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, quote, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from .config import get_settings

log = logging.getLogger(__name__)

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

# How long a video's details are remembered, so looking the same link up
# again doesn't ask YouTube again.
INFO_CACHE_SECONDS = 600
INFO_CACHE_SIZE = 64
# Seconds to wait before each retry after a bot check; the last one repeats.
RETRY_DELAYS = (4, 10, 20)

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


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def is_playlist_url(url: str) -> bool:
    """True for a link to a playlist itself, not to one video inside it."""
    parsed = urlparse(url)
    return parsed.path.rstrip("/") == "/playlist" and bool(parse_qs(parsed.query).get("list"))


def content_disposition(filename: str) -> str:
    """A Content-Disposition header value that survives non-ASCII names."""
    return f"attachment; filename*=utf-8\'\'{quote(filename)}"


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
    settings = get_settings()
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
    }
    if settings.yt_request_sleep > 0:
        opts["sleep_interval_requests"] = settings.yt_request_sleep
    if settings.yt_proxy:
        opts["proxy"] = settings.yt_proxy
    if settings.yt_pot_provider_url:
        opts["extractor_args"] = {
            # Read by the bgutil-ytdlp-pot-provider plugin.
            "youtubepot-bgutilhttp": {"base_url": [settings.yt_pot_provider_url]},
            # yt-dlp's default clients don't ask for a PO token, so the provider
            # would sit unused; the mweb client is the one that uses it.
            "youtube": {"player_client": ["default", "mweb"]},
        }
    return opts


@contextmanager
def _youtube_dl(extra: dict[str, Any] | None = None) -> Iterator[yt_dlp.YoutubeDL]:
    """A YoutubeDL with the app's settings applied."""
    opts = {**_base_opts(), **(extra or {})}
    cookies = get_settings().yt_cookies_file
    scratch: Path | None = None
    try:
        if cookies and cookies.is_file():
            # yt-dlp writes cookies back when it closes, so give it a private
            # copy: the original may be mounted read-only or used concurrently.
            handle = tempfile.NamedTemporaryFile(prefix="cookies-", suffix=".txt", delete=False)
            handle.close()
            scratch = Path(handle.name)
            shutil.copyfile(cookies, scratch)
            opts["cookiefile"] = str(scratch)
        elif cookies:
            log.warning("YT_COOKIES_FILE is set but %s does not exist", cookies)
        with yt_dlp.YoutubeDL(opts) as ydl:
            yield ydl
    finally:
        if scratch:
            scratch.unlink(missing_ok=True)


class _Throttle:
    """Spaces calls out so that they start at least `interval` seconds apart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self, interval: float) -> None:
        if interval <= 0:
            return
        with self._lock:
            current = time.monotonic()
            delay = max(self._next - current, 0.0)
            self._next = current + delay + interval
        if delay:
            time.sleep(delay)


class _InfoCache:
    """Recently fetched video details (what probe() returns), by link.

    Only the details are kept, never yt-dlp's stream links: YouTube rejects
    those with a 403 when a different yt-dlp session tries to download them,
    so every conversion has to fetch its own.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def put(self, url: str, info: dict[str, Any]) -> None:
        with self._lock:
            self._entries.pop(url, None)
            self._entries[url] = (time.monotonic(), info)
            while len(self._entries) > INFO_CACHE_SIZE:
                self._entries.pop(next(iter(self._entries)))

    def take(self, url: str) -> dict[str, Any] | None:
        """Return a private copy of the metadata for `url`, if still fresh."""
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None
            stored_at, info = entry
            if time.monotonic() - stored_at > INFO_CACHE_SECONDS:
                del self._entries[url]
                return None
        return copy.deepcopy(info)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_throttle = _Throttle()
_info_cache = _InfoCache()


def _is_bot_check(message: str) -> bool:
    return "confirm you" in message and "not a bot" in message


def _extract(
    ydl: yt_dlp.YoutubeDL, url: str, on_retry: Callable[[int], None] | None = None
) -> dict[str, Any]:
    """Fetch metadata for `url`, paced, retrying when YouTube's bot check hits."""
    settings = get_settings()
    attempt = 0
    while True:
        _throttle.wait(settings.yt_min_interval)
        try:
            return ydl.extract_info(url, download=False)
        except DownloadError as error:
            if not _is_bot_check(str(error)) or attempt >= settings.yt_retries:
                raise
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
        attempt += 1
        log.warning("YouTube bot check; retry %d of %d in %ds", attempt, settings.yt_retries, delay)
        if on_retry:
            on_retry(attempt)
        time.sleep(delay)


def _friendly(error: DownloadError) -> ConversionError:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    if _is_bot_check(message):
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
        "kind": "video",
        "video_id": info.get("id"),
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": int(info["duration"]) if info.get("duration") else None,
        "uploader": info.get("uploader") or info.get("channel"),
        "max_height": _max_height(info),
    }


def _playlist_summary(info: dict[str, Any], limit: int) -> dict[str, Any]:
    entries = []
    for entry in info.get("entries") or []:
        video_id = (entry or {}).get("id")
        # Private and deleted videos are listed without a duration or channel.
        if not video_id or entry.get("title") in ("[Private video]", "[Deleted video]"):
            continue
        entries.append(
            {
                "video_id": video_id,
                "title": entry.get("title"),
                "duration": int(entry["duration"]) if entry.get("duration") else None,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            }
        )
    total = info.get("playlist_count") or len(entries)
    return {
        "kind": "playlist",
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "entries": entries[:limit],
        "total": total,
        "truncated": total > limit,
    }


def probe(url: str) -> dict[str, Any]:
    """Return details of a video, or of a playlist and its videos, without downloading."""
    url = validate_url(url)
    cached = _info_cache.take(url)
    if cached is not None:
        return cached
    try:
        if is_playlist_url(url):
            # List the playlist's videos without fetching each one.
            limit = get_settings().max_playlist_items
            flat = {"noplaylist": False, "extract_flat": "in_playlist", "playlistend": limit}
            with _youtube_dl(flat) as ydl:
                summary = _playlist_summary(_extract(ydl, url), limit)
            if not summary["entries"]:
                raise ConversionError("This playlist has no videos that can be converted.")
        else:
            with _youtube_dl() as ydl:
                summary = _summary(_extract(ydl, url))
    except DownloadError as error:
        raise _friendly(error) from error
    _info_cache.put(url, summary)
    return copy.deepcopy(summary)


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
        **format_opts,
        "outtmpl": str(out_dir / f"{job_id}.%(ext)s"),
        "progress_hooks": [download_hook],
        "postprocessor_hooks": [postprocess_hook],
    }

    try:
        with _youtube_dl(opts) as ydl:
            info = _extract(ydl, url, on_retry=lambda attempt: on_progress("retrying", 0.0))
            summary = _summary(info)
            _info_cache.put(url, summary)
            on_info(copy.deepcopy(summary))
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


def remove_files(out_dir: Path, job_id: str) -> None:
    """Delete every file that belongs to `job_id`, finished or partial."""
    _remove_leftovers(out_dir, job_id)


def _remove_leftovers(out_dir: Path, job_id: str, keep: Path | None = None) -> None:
    for path in out_dir.glob(f"{job_id}.*"):
        if path != keep:
            path.unlink(missing_ok=True)
