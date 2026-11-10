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
    assert format_options("mp4", "best")["format_sort"] == ["vcodec:h264", "acodec:m4a"]
    assert format_options("mkv", "best")["format_sort"] == []


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
