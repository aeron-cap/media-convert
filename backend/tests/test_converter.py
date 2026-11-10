import pytest

from app.converter import (
    ConversionError,
    _download_fraction,
    _stream_sizes,
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
