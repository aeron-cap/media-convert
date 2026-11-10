"""Input checks, limits and the error messages shown for YouTube failures."""

import threading

import pytest
from yt_dlp.utils import DownloadError

from app import converter
from app.config import get_settings
from app.converter import ConversionError, _friendly, validate_url

from .conftest import wait_for_job

URL = "https://www.youtube.com/watch?v=abc123"


# --- links ---


@pytest.mark.parametrize(
    "url",
    [
        # The real host is whatever follows the "@".
        "https://www.youtube.com@evil.test/watch?v=abc123",
        "https://user:pass@www.youtube.com/watch?v=abc123",
        "https://www.youtube.com:8443/watch?v=abc123",
        "https://www.youtube.com:notaport/watch?v=abc123",
        "https://www.youtube.com/watch?v=abc 123",
        "https://www.youtube.com/watch?v=abc\n123",
        "https://evil.test/?https://www.youtube.com/watch?v=abc123",
        "https://youtube.com.evil.test/watch?v=abc123",
        "javascript:alert(1)//www.youtube.com",
        "//www.youtube.com/watch?v=abc123",
        "https://[::1]/watch?v=abc123",
        "http://127.0.0.1/watch?v=abc123",
        "https://",
        "",
    ],
)
def test_look_alike_links_are_refused(url):
    with pytest.raises(ConversionError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://WWW.YouTube.com/watch?v=abc123",
        "https://www.youtube.com:443/watch?v=abc123",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/playlist?list=PLabc",
    ],
)
def test_ordinary_youtube_links_still_pass(url):
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/@Blender",
        "https://www.youtube.com/channel/UCabc/videos",
        # Search results and feeds are endless lists.
        "https://www.youtube.com/results?search_query=blender",
        "https://www.youtube.com/feed/subscriptions",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/playlist",
        "https://www.youtube.com/shorts/",
        "https://youtu.be/",
    ],
)
def test_links_that_are_not_a_video_or_playlist_are_refused(url):
    with pytest.raises(ConversionError, match="isn't a video or a playlist"):
        validate_url(url)


def test_only_youtube_video_and_playlist_extractors_may_run():
    def handled(ydl, url):
        return any(ie.suitable(url) for ie in ydl._ies.values())

    with converter._youtube_dl() as ydl:
        assert set(ydl._ies) == {
            "Youtube", "YoutubeTab", "YoutubePlaylist", "YoutubeTruncatedID", "YoutubeTruncatedURL",
        }
        assert handled(ydl, "https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        assert handled(ydl, "https://youtu.be/aqz-KE-bpKQ")
        assert handled(ydl, "https://www.youtube.com/shorts/aqz-KE-bpKQ")
        assert handled(ydl, "https://www.youtube.com/playlist?list=PLabc")
        assert not handled(ydl, "https://example.com/video.mp4")
        assert not handled(ydl, "https://www.youtube.com/results?search_query=blender")


# --- error messages ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Private video. Sign in if you've been granted access to this video", "private"),
        ("Sign in to confirm your age. This video may be inappropriate for some users.", "age-restricted"),
        ("Join this channel to get access to members-only content like this video", "members only"),
        ("This video is available to this channel's members on level: Gold", "members only"),
        ("This video requires payment to watch", "bought or rented"),
        ("Video unavailable. The uploader has not made this video available in your country", "country"),
        ("Premieres in 3 hours", "hasn't started"),
        ("This live event will begin in 2 days.", "hasn't started"),
        ("Video unavailable. This video has been removed by the uploader", "removed"),
        ("Video unavailable. This video is no longer available due to a copyright claim by X", "copyright"),
        ("Video unavailable", "unavailable on YouTube"),
        ("This video is unavailable", "unavailable on YouTube"),
        ("Incomplete YouTube ID abc. URL looks truncated.", "full link"),
        ("The playlist does not exist.", "playlist is private or doesn't exist"),
        ("Unable to download webpage: HTTP Error 429: Too Many Requests", "limiting requests"),
        ("Requested format is not available. Use --list-formats", "chosen format"),
        ("Unable to download webpage: [Errno -3] Temporary failure in name resolution", "couldn't reach YouTube"),
        ("Postprocessing: Conversion failed!", "converting the file failed"),
        ("unable to write data: [Errno 28] No space left on device", "disk space"),
    ],
)
def test_youtube_errors_are_explained_in_plain_words(raw, expected):
    message = str(_friendly(DownloadError(f"ERROR: [youtube] abc123: {raw}")))
    assert expected in message
    # None of yt-dlp's command-line advice reaches the user.
    assert "--" not in message
    assert "abc123" not in message


def test_unrecognised_errors_are_passed_on_tidied_and_shortened():
    assert str(_friendly(DownloadError("ERROR: [youtube] abc123: Something   new\nhappened"))) == (
        "Something new happened"
    )
    long = str(_friendly(DownloadError("ERROR: " + "x" * 5000)))
    assert len(long) == converter.MAX_ERROR_LENGTH
    assert long.endswith("…")
    assert str(_friendly(DownloadError(""))) == "The video could not be fetched."


def test_channel_links_are_explained():
    with pytest.raises(ConversionError, match="channel or a list"):
        converter._summary({"_type": "playlist", "entries": []})


# --- limits ---


def test_too_long_video_is_refused_at_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_DURATION_MINUTES", "1")
    get_settings.cache_clear()
    converter._info_cache.clear()
    monkeypatch.setattr(
        converter,
        "_extract",
        lambda ydl, url, on_retry=None: {"id": "abc123", "title": "Long", "duration": 61.0},
    )
    try:
        # Both the first lookup and the remembered one are refused.
        for _ in range(2):
            with pytest.raises(ConversionError, match="1-minute limit"):
                converter.probe(URL)
    finally:
        converter._info_cache.clear()
        get_settings.cache_clear()


def test_duration_limit_can_be_switched_off():
    converter.check_duration(10**6, 0)
    converter.check_duration(None, 5)
    converter.check_duration(300, 5)
    with pytest.raises(ConversionError):
        converter.check_duration(301, 5)


@pytest.fixture
def two_pending(monkeypatch):
    monkeypatch.setenv("MAX_PENDING_JOBS", "2")


@pytest.fixture
def held_convert(monkeypatch):
    """A converter that waits until the test lets it finish."""
    release = threading.Event()

    def convert(url, format, quality, out_dir, job_id, on_info, on_progress):
        release.wait(5)
        path = out_dir / f"{job_id}.{format}"
        path.write_bytes(b"fake media")
        return path

    monkeypatch.setattr(converter, "convert", convert)
    yield release
    release.set()


def test_new_conversions_are_refused_while_too_many_wait(two_pending, client, held_convert):
    first = client.post("/api/jobs", json={"url": URL, "format": "mp3"})
    second = client.post("/api/jobs", json={"url": URL, "format": "mp3"})
    assert (first.status_code, second.status_code) == (201, 201)

    refused = client.post("/api/jobs", json={"url": URL, "format": "mp3"})
    assert refused.status_code == 429
    assert "waiting" in refused.json()["detail"]
    batch = {
        "url": "https://www.youtube.com/playlist?list=PLabc",
        "title": "Mix",
        "format": "mp3",
        "entries": [{"video_id": "aaaaaaaaaaa", "title": "First"}],
    }
    assert client.post("/api/batches", json=batch).status_code == 429
    assert client.get("/api/batches").json() == []

    held_convert.set()
    wait_for_job(client, first.json()["id"])
    wait_for_job(client, second.json()["id"])
    again = client.post("/api/jobs", json={"url": URL, "format": "mp3"})
    assert again.status_code == 201
    wait_for_job(client, again.json()["id"])


def test_batch_refuses_the_same_video_twice(client, fake_convert):
    body = {
        "url": "https://www.youtube.com/playlist?list=PLabc",
        "title": "Mix",
        "format": "mp3",
        "entries": [{"video_id": "aaaaaaaaaaa"}, {"video_id": "aaaaaaaaaaa"}],
    }
    response = client.post("/api/batches", json=body)
    assert response.status_code == 422
    assert fake_convert == []


def test_oversized_input_is_refused(client, fake_convert):
    long_url = URL + "&x=" + "a" * 3000
    assert client.post("/api/jobs", json={"url": long_url, "format": "mp3"}).status_code == 422
    assert client.post("/api/probe", json={"url": long_url}).status_code == 422
    assert client.post("/api/jobs", json={"url": URL, "format": "mp3", "quality": "9" * 50}).status_code == 422


# --- responses ---


def test_security_headers_are_sent(client):
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in headers["content-security-policy"]
    # The API explorer loads scripts from a CDN, so it is exempt.
    assert "content-security-policy" not in client.get("/docs").headers


def test_downloads_are_always_attachments(client, fake_convert):
    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()
    job = wait_for_job(client, created["id"])
    download = client.get(job["download_url"])
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"


def test_job_ids_cannot_reach_other_files(client, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    for job_id in ("..%2Fsecret", "%2E%2E%2Fsecret.txt", "*", "..%5Csecret"):
        assert client.get(f"/api/jobs/{job_id}/file").status_code == 404
        assert client.delete(f"/api/jobs/{job_id}").status_code == 404
    assert (tmp_path / "secret.txt").exists()
