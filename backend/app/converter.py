"""Fetching from YouTube and TikTok (yt-dlp) and converting (ffmpeg, driven by yt-dlp)."""

import copy
import logging
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import ParseResult, parse_qs, quote, urlparse

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from . import thumbnails
from .config import get_settings

log = logging.getLogger(__name__)

Source = Literal["youtube", "tiktok"]


class ConversionError(Exception):
    """A failure with a message that is safe and useful to show to the user."""


@dataclass(frozen=True)
class Site:
    """A site videos can be converted from."""

    key: Source
    name: str
    hosts: frozenset[str]
    # yt-dlp extractors that may run for this site, by name.
    extractors: tuple[str, ...]
    # What a link to this site may lead to, for messages.
    noun: str
    # The link to fetch for an accepted link, or None if it doesn't lead to a
    # single video (or playlist). May raise ConversionError with a better reason.
    canonical: Callable[[ParseResult, str], str | None]
    # yt-dlp format selectors for video and audio output.
    video_selector: str
    audio_selector: str
    # Whether thumbnails are copied to this server (their links expire).
    stores_thumbnails: bool = False


# --- YouTube ---

# Paths that lead to one video, on every host except youtu.be (where the
# whole path is the video's id).
VIDEO_PATH_PREFIXES = ("/shorts/", "/live/", "/embed/", "/v/")


def _youtube_link(parsed: ParseResult, url: str) -> str | None:
    return url if _is_video_or_playlist(parsed) else None


def _is_video_or_playlist(parsed: Any) -> bool:
    path, query = parsed.path, parse_qs(parsed.query)
    if (parsed.hostname or "").lower() == "youtu.be":
        return len(path) > 1
    if path.rstrip("/") == "/watch":
        return bool(query.get("v"))
    if path.rstrip("/") == "/playlist":
        return bool(query.get("list"))
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in VIDEO_PATH_PREFIXES)


# --- TikTok ---

_TIKTOK_VIDEO = re.compile(r"/@(?P<user>[\w.-]*)/video/(?P<id>\d{1,32})/?")
_TIKTOK_SHARE = re.compile(r"/share/video/(?P<id>\d{1,32})/?")
_TIKTOK_SHORT = re.compile(r"/(?P<code>[A-Za-z0-9]{1,64})/?")
_TIKTOK_T = re.compile(r"/t/(?P<code>[A-Za-z0-9]{1,64})/?")
_TIKTOK_PHOTO = re.compile(r"/@[\w.-]*/photo/")


def _tiktok_link(parsed: ParseResult, url: str) -> str | None:
    """The link yt-dlp should fetch: its extractors only know www hosts."""
    host, path = (parsed.hostname or "").lower(), parsed.path
    if host in ("vm.tiktok.com", "vt.tiktok.com"):
        match = _TIKTOK_SHORT.fullmatch(path)
        return f"https://{host}/{match['code']}/" if match else None
    if host in ("tiktokv.com", "www.tiktokv.com"):
        match = _TIKTOK_SHARE.fullmatch(path)
        return f"https://www.tiktokv.com/share/video/{match['id']}/" if match else None
    if match := _TIKTOK_VIDEO.fullmatch(path):
        return f"https://www.tiktok.com/@{match['user']}/video/{match['id']}"
    if match := _TIKTOK_T.fullmatch(path):
        return f"https://www.tiktok.com/t/{match['code']}/"
    if _TIKTOK_PHOTO.match(path):
        raise ConversionError(
            "That link is a TikTok photo post, which has no video to convert. "
            "Use a link to a TikTok video."
        )
    return None


YOUTUBE = Site(
    key="youtube",
    name="YouTube",
    hosts=frozenset(
        {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
    ),
    # The two "truncated" ones only produce a clear error for links that
    # were cut off.
    extractors=(
        "youtube",
        "youtube:tab",
        "youtube:playlist",
        "youtube:truncated_id",
        "youtube:truncated_url",
    ),
    noun="video or playlist",
    canonical=_youtube_link,
    # YouTube offers video and audio separately; merge the best of each.
    video_selector="bv*+ba/b",
    audio_selector="bestaudio/best",
)

# TikTok offers each video as single files holding both video and audio,
# plus a watermarked "download" copy, plus the sound's music track. The
# music track can be a whole song rather than what the video plays, so audio
# is taken from the video file. The watermarked copy is only used if it is
# all there is (yt-dlp also ranks it last).
_TIKTOK_CLEAN = "b[format_note!*=?watermark]"
TIKTOK = Site(
    key="tiktok",
    name="TikTok",
    hosts=frozenset(
        {
            "tiktok.com",
            "www.tiktok.com",
            "m.tiktok.com",
            "vm.tiktok.com",
            "vt.tiktok.com",
            "tiktokv.com",
            "www.tiktokv.com",
        }
    ),
    # Single videos and short share links. Never the user, tag, sound,
    # effect, collection or live extractors: those are endless or live lists.
    extractors=("tiktok", "vm.tiktok"),
    noun="video",
    canonical=_tiktok_link,
    video_selector=f"{_TIKTOK_CLEAN}/bv*+ba/b",
    audio_selector=f"{_TIKTOK_CLEAN}/b/bestaudio",
    stores_thumbnails=True,
)

SITES: tuple[Site, ...] = (YOUTUBE, TIKTOK)

# yt-dlp extractors, as the regular expressions yt-dlp matches names with.
ALLOWED_EXTRACTORS = tuple(re.escape(name) for site in SITES for name in site.extractors)


def _site_of_host(host: str | None) -> Site | None:
    host = (host or "").lower()
    return next((site for site in SITES if host in site.hosts), None)


def site_of(url: str) -> Site:
    """The site a link (already checked by validate_url) belongs to."""
    try:
        site = _site_of_host(urlparse(url.strip()).hostname)
    except ValueError:
        site = None
    if site is None:
        raise ConversionError(_UNSUPPORTED_LINK)
    return site


def source_of(url: str) -> Source:
    """"youtube" or "tiktok" for a link that validate_url accepted."""
    return site_of(url).key


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
        "note": "Keeps the original AAC audio without re-encoding.",
    },
    "ogg": {"label": "OGG", "kind": "audio", "qualities": AUDIO_BITRATES},
    "flac": {"label": "FLAC", "kind": "audio", "qualities": [], "note": "Lossless, large files."},
    "wav": {"label": "WAV", "kind": "audio", "qualities": [], "note": "Uncompressed, very large files."},
    "mp4": {"label": "MP4", "kind": "video", "qualities": VIDEO_QUALITIES},
    "webm": {
        "label": "WEBM",
        "kind": "video",
        "qualities": VIDEO_QUALITIES,
        "note": "Slow for videos that aren't already offered as WEBM, which includes every TikTok video.",
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


_UNSUPPORTED_LINK = "Enter a YouTube or TikTok link (youtube.com, youtu.be or tiktok.com)."


def validate_url(url: str) -> str:
    """Check a link and return the one to fetch.

    YouTube links come back as they are; TikTok links are rewritten to the
    form yt-dlp recognises (www host, no tracking parameters).
    """
    url = url.strip()
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        parsed, port = None, None
    site = _site_of_host(parsed.hostname) if parsed is not None else None
    if (
        parsed is None
        or site is None
        or parsed.scheme not in ("http", "https")
        # A real link never carries a login or a port; refusing them keeps
        # look-alikes such as "youtube.com@elsewhere" out entirely.
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 80, 443)
        or any(ord(char) < 0x21 or ord(char) == 0x7F for char in url)
    ):
        raise ConversionError(_UNSUPPORTED_LINK)
    canonical = site.canonical(parsed, url)
    if canonical is None:
        # Channels, profiles, searches, feeds, tags: some are endless lists.
        if site is YOUTUBE:
            raise ConversionError(
                "That link isn't a video or a playlist. "
                "Open the video or playlist on YouTube and copy its link."
            )
        raise ConversionError(
            f"That link isn't a single {site.name} video. "
            f"Open the video on {site.name} and copy its link."
        )
    return canonical


def check_duration(duration: int | None, limit_minutes: int) -> None:
    """Raise if a video of `duration` seconds is over the server's limit."""
    if limit_minutes and (duration or 0) > limit_minutes * 60:
        raise ConversionError(f"This video is longer than the {limit_minutes}-minute limit.")


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def is_playlist_url(url: str) -> bool:
    """True for a link to a YouTube playlist itself, not to one video inside it."""
    parsed = urlparse(url)
    return (
        _site_of_host(parsed.hostname) is YOUTUBE
        and parsed.path.rstrip("/") == "/playlist"
        and bool(parse_qs(parsed.query).get("list"))
    )


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


def format_options(format: str, quality: str | None, source: Source = "youtube") -> dict[str, Any]:
    """The yt-dlp options that produce `format` at `quality` from `source`."""
    quality = normalize_quality(format, quality)
    site = TIKTOK if source == "tiktok" else YOUTUBE

    if FORMATS[format]["kind"] == "audio":
        extract: dict[str, Any] = {"key": "FFmpegExtractAudio"}
        if format == "m4a":
            # An AAC source is copied into the M4A container untouched.
            # TikTok's videos carry AAC audio, so its selector already fits.
            selector = "bestaudio[ext=m4a]/bestaudio/best" if site is YOUTUBE else site.audio_selector
            options = {
                "format": selector,
                "postprocessors": [{**extract, "preferredcodec": "m4a"}],
            }
        else:
            extract["preferredcodec"] = "vorbis" if format == "ogg" else format
            if quality:
                extract["preferredquality"] = quality
            options = {"format": site.audio_selector, "postprocessors": [extract]}
        if site is TIKTOK:
            # yt-dlp would otherwise take an H.265 file, and TikTok's H.265
            # links are the ones that sometimes fail. The audio is the same.
            options["format_sort"] = ["vcodec:h264"]
        return options

    # Video. yt-dlp ranks by the listed fields in order, ahead of its own
    # defaults, so resolution and frame rate must be named before any codec:
    # with a codec first, "best" MP4 once chose a 360p H.264 stream over 4K.
    # Codecs then only break ties, so each container gets streams it can hold
    # without re-encoding where they exist.
    sort = [] if quality == "best" else [f"res:{quality}"]
    ranked = [*(sort or ["res"]), "fps"]
    if format == "mp4":
        # H.264 + AAC plays everywhere. Other codecs (above 1080p) are still
        # copied as they are rather than re-encoded.
        return {
            "format": site.video_selector,
            "format_sort": [*ranked, "vcodec:h264", "acodec:m4a"],
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }
    if format == "webm":
        # VP9 + Opus streams merge straight into WEBM. Anything else is
        # re-encoded by the convertor, which does nothing for a WEBM input.
        return {
            "format": site.video_selector,
            "format_sort": [*ranked, "vcodec:vp9", "acodec:opus"],
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "webm"}],
        }
    # MKV holds any codec, so take the best streams as they are.
    return {
        "format": site.video_selector,
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
        # yt-dlp knows well over a thousand sites; only its code for YouTube
        # videos and playlists and TikTok videos may run (not searches,
        # feeds, profiles or other sites).
        "allowed_extractors": list(ALLOWED_EXTRACTORS),
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
            # Normal when no cookies file is mounted; `GET /api/health` shows it.
            log.debug("YT_COOKIES_FILE is set but %s does not exist", cookies)
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


# What to tell the user for the errors yt-dlp reports most. yt-dlp's own text
# is advice for its command line ("use --cookies"), not for this app. Each
# entry is (phrases, message): the first entry with a phrase found in the
# error, ignoring case, wins, so the more specific ones come first. In the
# messages, {site} is the site's name and {noun} what its links may lead to.
_ERROR_MESSAGES: tuple[tuple[tuple[str, ...], str], ...] = (
    # TikTok's own wording first: some of it would match the general entries.
    (
        ("ip address is blocked from accessing this post",),
        "{site} doesn't show this video in the country this server is in, "
        "so it can't be converted here.",
    ),
    (
        ("permission to view this post",),
        "This video or its account is private on {site}. Only public videos can be converted.",
    ),
    (
        ("not be comfortable for some audiences",),
        "This video is age-restricted, so {site} only shows it to a signed-in account. "
        "It can't be converted unless this server is set up with account cookies.",
    ),
    (
        ("requiring login", "log in for access", "login required", "this content isn't available to everyone"),
        "{site} only shows this video to a signed-in account, so it can't be converted here.",
    ),
    (
        ("unexpected response from webpage request", "unable to solve js challenge", "unable to extract challenge data"),
        "{site} refused this server's request. Wait a few minutes and try again; "
        "if it keeps happening, the app may need an update.",
    ),
    (
        ("video not available, status code", "unable to extract universal data", "unable to extract webpage video data"),
        "This video is unavailable on {site}. It may have been removed, or its account made private.",
    ),
    (
        ("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"),
        "This video is age-restricted, so {site} only shows it to a signed-in account. "
        "It can't be converted unless this server is set up with account cookies.",
    ),
    (
        ("private video", "video is private"),
        "This video is private. Only public and unlisted videos can be converted.",
    ),
    (
        ("members-only", "members only", "channel's members", "join this channel"),
        "This video is for channel members only and can't be converted.",
    ),
    (
        ("requires payment",),
        "This video has to be bought or rented and can't be converted.",
    ),
    (
        ("not available in your country", "not made this video available", "geo-restricted", "geo restricted"),
        "This video is blocked in the country this server is in.",
    ),
    (
        ("premieres in", "live event will begin", "will begin in", "is_upcoming"),
        "This video hasn't started yet. Try again once it has been published.",
    ),
    (
        ("playlist does not exist", "playlist is private", "playlist type is unviewable"),
        "This playlist is private or doesn't exist.",
    ),
    (
        ("copyright",),
        "This video is blocked on {site} because of a copyright claim.",
    ),
    (
        ("removed by the uploader", "has been removed", "has been terminated", "no longer available"),
        "This video has been removed from {site}.",
    ),
    (
        # A short link that leads somewhere else, e.g. to TikTok's home page
        # once the video is gone.
        ("no suitable extractor",),
        "That link didn't lead to a {site} {noun}. "
        "The link may have expired, or the {noun} may have been removed.",
    ),
    (
        ("incomplete youtube id", "unsupported url", "is not a valid url"),
        "That link doesn't point to a {site} {noun}. Copy the full link and try again.",
    ),
    (
        ("http error 429", "too many requests"),
        "{site} is limiting requests from this server. Wait a few minutes and try again.",
    ),
    (
        ("requested format is not available",),
        "{site} doesn't offer this video in a form that can be converted to the chosen format.",
    ),
    (
        ("name resolution", "getaddrinfo", "timed out", "connection refused", "connection reset", "network is unreachable", "unable to download webpage", "unable to connect"),
        "This server couldn't reach {site}. Check its internet connection and try again.",
    ),
    (
        ("no space left on device",),
        "The server ran out of disk space. Remove old conversions and try again.",
    ),
    (
        ("postprocessing", "ffmpeg", "ffprobe"),
        "The download worked but converting the file failed. Try another format or quality.",
    ),
    (
        ("unavailable", "not available"),
        "This video is unavailable on {site}.",
    ),
)
# Longest text of an unrecognised error passed through to the user.
MAX_ERROR_LENGTH = 300


def _friendly(error: DownloadError, site: Site = YOUTUBE) -> ConversionError:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    if _is_bot_check(message):
        return ConversionError(
            f"{site.name} asked this server to prove it isn't a bot. "
            "This is usually temporary; wait a minute and try again."
        )
    lowered = message.lower().replace("’", "'")
    for phrases, text in _ERROR_MESSAGES:
        if any(phrase in lowered for phrase in phrases):
            text = text.format(site=site.name, noun=site.noun)
            log.info("yt-dlp error shown as %r: %s", text, message)
            return ConversionError(text)
    message = re.sub(r"^ERROR:\s*", "", message)
    message = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", message)
    message = " ".join(message.split())
    if len(message) > MAX_ERROR_LENGTH:
        message = message[: MAX_ERROR_LENGTH - 1].rstrip() + "…"
    return ConversionError(message or "The video could not be fetched.")


def _max_height(info: dict[str, Any]) -> int | None:
    heights = [
        f["height"]
        for f in info.get("formats") or []
        if f.get("height") and f.get("vcodec") not in (None, "none")
    ]
    return max(heights) if heights else None


def _summary(
    info: dict[str, Any], site: Site = YOUTUBE, ydl: yt_dlp.YoutubeDL | None = None
) -> dict[str, Any]:
    """The details shown for a video. With `ydl`, a thumbnail whose link
    expires is copied to this server first."""
    if info.get("_type") == "playlist":
        # Channels, searches and the like; real playlists are handled by probe().
        if site is YOUTUBE:
            hint = "Use a link to a single video or to a playlist (youtube.com/playlist?list=…)."
        else:
            hint = f"Use a link to a single {site.name} video."
        raise ConversionError(f"That link is a channel or a list, not a video. {hint}")
    if info.get("is_live"):
        raise ConversionError("Live streams can't be converted while they are live.")
    thumbnail = info.get("thumbnail")
    if site.stores_thumbnails:
        thumbnail = _stored_thumbnail(ydl, site, info) if ydl is not None else None
    return {
        "kind": "video",
        "source": site.key,
        "video_id": info.get("id"),
        "title": info.get("title"),
        "thumbnail": thumbnail,
        "duration": int(info["duration"]) if info.get("duration") else None,
        "uploader": info.get("uploader") or info.get("channel"),
        "max_height": _max_height(info),
    }


# Seconds to wait for a thumbnail before giving up on it.
THUMBNAIL_TIMEOUT = 15


def _stored_thumbnail(ydl: yt_dlp.YoutubeDL, site: Site, info: dict[str, Any]) -> str | None:
    """Copy the video's thumbnail to this server; its app path, or None.

    Never fails: a video without a thumbnail can still be converted.
    """
    name = thumbnails.name_for(site.key, str(info.get("id") or ""))
    link = info.get("thumbnail")
    if name is None or not isinstance(link, str) or not link.startswith("https://"):
        return None
    folder = get_settings().thumbs_dir
    if thumbnails.touch(folder, name):
        return thumbnails.app_path(name)
    try:
        data, content_type = _fetch_image(ydl, link, info.get("webpage_url"))
        return thumbnails.save(folder, name, data, content_type)
    except Exception as error:  # noqa: BLE001 - any failure just means no thumbnail
        log.info("no thumbnail for %s: %s", name, error)
        return None


def _fetch_image(ydl: yt_dlp.YoutubeDL, link: str, referer: str | None) -> tuple[bytes, str]:
    """Download an image through yt-dlp's own network stack (its proxy and,
    when available, its browser impersonation), refusing anything that isn't
    a small image."""
    extensions: dict[str, Any] = {"timeout": THUMBNAIL_TIMEOUT}
    target = ImpersonateTarget()
    if ydl._impersonate_target_available(target):
        extensions["impersonate"] = target
    headers = {"Referer": referer} if referer else {}
    limit = thumbnails.MAX_THUMBNAIL_BYTES
    with ydl.urlopen(Request(link, headers=headers, extensions=extensions)) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in thumbnails.IMAGE_TYPES:
            raise ValueError(f"not a supported image: {content_type!r}")
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > limit:
            raise ValueError(f"image too large: {length} bytes")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("image too large")
    if not data:
        raise ValueError("empty image")
    return data, content_type


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
        return _within_limits(cached)
    site = site_of(url)
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
                summary = _summary(_extract(ydl, url), site, ydl)
    except DownloadError as error:
        raise _friendly(error, site) from error
    _info_cache.put(url, summary)
    return _within_limits(copy.deepcopy(summary))


def _within_limits(summary: dict[str, Any]) -> dict[str, Any]:
    """Refuse a video that is too long up front, not after the user starts it."""
    if summary["kind"] == "video":
        check_duration(summary["duration"], get_settings().max_duration_minutes)
    return summary


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
    site = site_of(url)
    format_opts = format_options(format, quality, site.key)
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
            summary = _summary(info, site, ydl)
            _info_cache.put(url, summary)
            on_info(copy.deepcopy(summary))
            streams.extend(_stream_sizes(info))
            ydl.process_ie_result(info, download=True)
    except DownloadError as error:
        _remove_leftovers(out_dir, job_id)
        raise _friendly(error, site) from error
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
