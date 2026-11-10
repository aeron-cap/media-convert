import pytest

from app.converter import (
    FORMATS,
    ConversionError,
    _download_fraction,
    _stream_sizes,
    format_options,
    normalize_quality,
    safe_filename,
    validate_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://music.youtube.com/watch?v=abc123",
        "  https://m.youtube.com/watch?v=abc123  ",
    ],
)
def test_validate_url_accepts_youtube(url):
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc123",
        "https://youtube.com.evil.test/watch?v=abc123",
        "ftp://www.youtube.com/watch?v=abc123",
        "file:///etc/passwd",
        "not a url",
    ],
)
def test_validate_url_rejects_everything_else(url):
    with pytest.raises(ConversionError):
        validate_url(url)


def test_safe_filename():
    assert safe_filename('a/b\\c: "d"?', "x") == "a b c d"
    assert safe_filename("...", "fallback") == "fallback"
    assert safe_filename(None, "fallback") == "fallback"
    assert len(safe_filename("a" * 500, "x")) == 150


def test_download_fraction_single_stream():
    assert _download_fraction({"downloaded_bytes": 25, "total_bytes": 100}, []) == 0.25
    assert _download_fraction({"downloaded_bytes": 25}, []) == 0.0


def test_download_fraction_spans_video_and_audio_streams():
    info = {
        "requested_formats": [
            {"format_id": "v", "filesize": 300},
            {"format_id": "a", "filesize_approx": 100},
        ]
    }
    streams = _stream_sizes(info)
    assert streams == [("v", 300), ("a", 100)]

    def fraction(current, downloaded):
        hook_data = {
            "downloaded_bytes": downloaded,
            "total_bytes": 999,
            "info_dict": {"format_id": current},
        }
        return _download_fraction(hook_data, streams)

    assert fraction("v", 150) == 0.375
    assert fraction("a", 50) == 0.875


def test_stream_sizes_unknown_when_a_size_is_missing():
    info = {"requested_formats": [{"format_id": "v", "filesize": 300}, {"format_id": "a"}]}
    assert _stream_sizes(info) == []
    assert _stream_sizes({}) == []


@pytest.mark.parametrize("format", list(FORMATS))
def test_every_format_has_options_for_every_quality(format):
    for quality in [None, *FORMATS[format]["qualities"]]:
        options = format_options(format, quality)
        assert options["format"]
        assert options["postprocessors"]


def test_audio_bitrate_is_passed_to_the_encoder():
    (extract,) = format_options("mp3", "320")["postprocessors"]
    assert extract == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}
    # OGG files hold Vorbis audio.
    assert format_options("ogg", "128")["postprocessors"][0]["preferredcodec"] == "vorbis"


def test_video_quality_caps_the_resolution():
    assert format_options("mp4", "720")["format_sort"][0] == "res:720"
    assert format_options("mkv", "best")["format_sort"] == []


@pytest.mark.parametrize("format", ["mp4", "webm"])
@pytest.mark.parametrize("quality", ["best", "1080", "360"])
def test_resolution_outranks_the_codec_preference(format, quality):
    # yt-dlp ranks by these fields in order. A codec listed before the
    # resolution makes "best" settle for a low-resolution stream in that codec.
    sort = format_options(format, quality)["format_sort"]
    fields = [entry.split(":")[0] for entry in sort]
    assert fields[:2] == ["res", "fps"]
    assert fields.index("res") < fields.index("vcodec") < fields.index("acodec")
    assert sort[0] == ("res" if quality == "best" else f"res:{quality}")


def test_best_mp4_picks_the_highest_resolution_stream():
    """The real selection, run by yt-dlp on a made-up list of streams."""
    from app.converter import _youtube_dl

    def stream(format_id, height, vcodec, acodec="none", ext="mp4"):
        video = vcodec != "none"
        return {
            "format_id": format_id, "url": f"https://example.test/{format_id}", "ext": ext,
            "protocol": "https", "vcodec": vcodec, "acodec": acodec,
            "height": height if video else None, "width": height * 16 // 9 if video else None,
            "fps": 30 if video else None, "tbr": height or 128,
        }

    def chosen(format, quality, formats):
        info = {"id": "abc", "title": "t", "extractor": "youtube", "formats": formats}
        with _youtube_dl({**format_options(format, quality), "simulate": True}) as ydl:
            result = ydl.process_video_result(info, download=False)
        return [part["format_id"] for part in result.get("requested_formats") or [result]]

    combined = stream("18", 360, "avc1.42001E", "mp4a.40.2")
    audio = [stream("140", 0, "none", "mp4a.40.2", "m4a"), stream("251", 0, "none", "opus", "webm")]
    h264 = [stream("136", 720, "avc1.4d401f"), stream("137", 1080, "avc1.640028")]
    vp9 = [stream("248", 1080, "vp9", ext="webm"), stream("313", 2160, "vp9", ext="webm")]

    # 4K exists only as VP9: take it, not the best H.264 and not the 360p file.
    assert chosen("mp4", "best", [combined, *audio, *h264, *vp9]) == ["313", "140"]
    # At a resolution both codecs offer, H.264 wins for MP4 and VP9 for WEBM.
    assert chosen("mp4", "1080", [combined, *audio, *h264, *vp9]) == ["137", "140"]
    assert chosen("webm", "1080", [combined, *audio, *h264, *vp9]) == ["248", "251"]
    assert chosen("mp4", "best", [combined, *audio, *h264]) == ["137", "140"]


def test_normalize_quality():
    assert normalize_quality("mp3", None) == "192"
    assert normalize_quality("webm", None) == "best"
    assert normalize_quality("wav", "320") is None
    with pytest.raises(ConversionError):
        normalize_quality("mp4", "320")
    with pytest.raises(ConversionError):
        normalize_quality("exe", None)


def test_old_database_gains_the_quality_column(tmp_path):
    import sqlite3

    from app.db import Database

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, url TEXT NOT NULL, format TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'queued', stage TEXT, progress REAL NOT NULL DEFAULT 0, "
        "error TEXT, video_id TEXT, title TEXT, thumbnail TEXT, duration INTEGER, filename TEXT, "
        "filesize INTEGER, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)"
    )
    conn.execute("INSERT INTO jobs (id, url, format, created_at) VALUES ('a', 'u', 'mp3', 't')")
    conn.commit()
    conn.close()

    db = Database(path)
    db.init()
    db.init()  # running the migration twice is harmless

    assert db.get_job("a")["quality"] is None
    assert db.create_job("u", "mp4", "720")["quality"] == "720"


def test_bot_check_error_gets_a_plain_message():
    from yt_dlp.utils import DownloadError

    from app.converter import _friendly

    raw = "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser"
    message = str(_friendly(DownloadError(raw)))
    assert "try again" in message
    assert "--cookies" not in message
    assert str(_friendly(DownloadError("ERROR: [youtube] abc: Video unavailable"))) == (
        "This video is unavailable on YouTube."
    )
