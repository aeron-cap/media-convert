"""TikTok videos: lookups, stored thumbnails, the source field and clean-up."""

import os
import time
from contextlib import contextmanager

import pytest

from app import converter, thumbnails
from app.cleanup import ORPHAN_AGE, run_cleanup
from app.config import get_settings
from app.db import Database
from app.models import Job

from .conftest import wait_for_job

VIDEO_ID = "7047596209028074758"
URL = f"https://www.tiktok.com/@hankgreen1/video/{VIDEO_ID}"
THUMB_PATH = f"/api/thumbnails/tiktok-{VIDEO_ID}"
JPEG = b"\xff\xd8\xff\xe0 fake jpeg"


def tiktok_info(**changes):
    info = {
        "id": VIDEO_ID,
        "title": "A TikTok",
        "duration": 33,
        "uploader": "hankgreen1",
        "channel": "Hank Green",
        "thumbnail": "https://p16-common-sign.tiktokcdn.com/obj/cover.jpeg?x-expires=1",
        "webpage_url": URL,
        "formats": [{"format_id": "h264_540p", "vcodec": "h264", "acodec": "aac", "height": 1024}],
    }
    info.update(changes)
    return info


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YT_MIN_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()
    converter._info_cache.clear()
    yield tmp_path
    converter._info_cache.clear()
    get_settings.cache_clear()


@pytest.fixture
def fake_lookup(monkeypatch):
    """Answer lookups with `state["info"]` and thumbnails with `state["image"]`."""
    state = {"info": tiktok_info(), "image": (JPEG, "image/jpeg"), "fetched": []}

    def extract(ydl, url, on_retry=None):
        return dict(state["info"])

    def fetch_image(ydl, link, referer):
        state["fetched"].append(link)
        if isinstance(state["image"], Exception):
            raise state["image"]
        return state["image"]

    monkeypatch.setattr(converter, "_extract", extract)
    monkeypatch.setattr(converter, "_fetch_image", fetch_image)
    return state


# --- lookups ---


def test_tiktok_lookup_has_its_source_and_a_stored_thumbnail(data_dir, fake_lookup):
    summary = converter.probe(URL + "?is_from_webapp=1")
    assert summary == {
        "kind": "video",
        "source": "tiktok",
        "video_id": VIDEO_ID,
        "title": "A TikTok",
        "thumbnail": THUMB_PATH,
        "duration": 33,
        "uploader": "hankgreen1",
        "max_height": 1024,
    }
    assert (data_dir / "thumbs" / f"tiktok-{VIDEO_ID}.jpg").read_bytes() == JPEG


def test_a_stored_thumbnail_is_not_fetched_again(data_dir, fake_lookup):
    converter.probe(URL)
    converter._info_cache.clear()
    converter.probe(URL)
    assert len(fake_lookup["fetched"]) == 1


@pytest.mark.parametrize(
    "image",
    [ValueError("not a supported image: 'text/html'"), OSError("connection reset")],
)
def test_a_failed_thumbnail_does_not_fail_the_lookup(data_dir, fake_lookup, image):
    fake_lookup["image"] = image
    summary = converter.probe(URL)
    assert summary["thumbnail"] is None
    assert summary["title"] == "A TikTok"
    assert not list((data_dir / "thumbs").glob("*"))


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../../media-convert"},
        {"id": "7047596209028074758/../x"},
        {"id": None},
        {"thumbnail": None},
        {"thumbnail": "http://insecure.test/cover.jpeg"},
    ],
)
def test_odd_ids_and_links_give_no_thumbnail(data_dir, fake_lookup, changes):
    fake_lookup["info"] = tiktok_info(**changes)
    assert converter.probe(URL)["thumbnail"] is None
    assert fake_lookup["fetched"] == []
    assert not (data_dir / "thumbs").exists()


def test_youtube_lookups_keep_their_own_thumbnail_links(data_dir, fake_lookup):
    fake_lookup["info"] = {
        "id": "aqz-KE-bpKQ",
        "title": "Big Buck Bunny",
        "thumbnail": "https://i.ytimg.com/vi/aqz-KE-bpKQ/maxresdefault.jpg",
        "duration": 635,
    }
    summary = converter.probe("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
    assert summary["source"] == "youtube"
    assert summary["thumbnail"] == "https://i.ytimg.com/vi/aqz-KE-bpKQ/maxresdefault.jpg"
    assert fake_lookup["fetched"] == []


def test_tiktok_lookups_are_paced_like_youtube_ones(data_dir, monkeypatch):
    waits = []
    monkeypatch.setattr(converter._throttle, "wait", waits.append)
    monkeypatch.setenv("YT_MIN_INTERVAL_SECONDS", "3")
    get_settings.cache_clear()

    class FakeYDL:
        def extract_info(self, url, download):
            return {"id": VIDEO_ID}

    converter._extract(FakeYDL(), URL)
    assert waits == [3.0]


def test_probe_endpoint_for_tiktok(client, fake_lookup):
    response = client.post("/api/probe", json={"url": URL})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "tiktok"
    assert body["thumbnail"] == THUMB_PATH

    image = client.get(THUMB_PATH)
    assert image.status_code == 200
    assert image.content == JPEG
    assert image.headers["content-type"] == "image/jpeg"
    assert "max-age" in image.headers["cache-control"]
    assert image.headers["x-content-type-options"] == "nosniff"
    converter._info_cache.clear()


def test_tiktok_profile_lookup_is_refused(client):
    response = client.post("/api/probe", json={"url": "https://www.tiktok.com/@hankgreen1"})
    assert response.status_code == 422
    assert "single TikTok video" in response.json()["detail"]


# --- fetching the image ---


class FakeResponse:
    def __init__(self, body, headers):
        self.body = body
        self.headers = headers
        self.asked = None

    def read(self, amount):
        self.asked = amount
        return self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ImageYDL:
    def __init__(self, response, impersonation=True):
        self.response = response
        self.impersonation = impersonation
        self.requests = []

    def _impersonate_target_available(self, target):
        return self.impersonation

    def urlopen(self, request):
        self.requests.append(request)
        return self.response


def test_fetch_image_uses_yt_dlp_with_impersonation():
    ydl = ImageYDL(FakeResponse(JPEG, {"Content-Type": "image/jpeg; charset=binary"}))
    assert converter._fetch_image(ydl, "https://cdn.test/a.jpeg", URL) == (JPEG, "image/jpeg")
    (request,) = ydl.requests
    assert "impersonate" in request.extensions
    assert request.headers["Referer"] == URL
    assert ydl.response.asked == thumbnails.MAX_THUMBNAIL_BYTES + 1

    plain = ImageYDL(FakeResponse(JPEG, {"Content-Type": "image/webp"}), impersonation=False)
    assert converter._fetch_image(plain, "https://cdn.test/a", None) == (JPEG, "image/webp")
    assert "impersonate" not in plain.requests[0].extensions


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"<html>", {"Content-Type": "text/html"}),
        (b"<svg/>", {"Content-Type": "image/svg+xml"}),
        (JPEG, {}),
        (JPEG, {"Content-Type": "image/jpeg", "Content-Length": str(10**7)}),
        (b"x" * (thumbnails.MAX_THUMBNAIL_BYTES + 1), {"Content-Type": "image/jpeg"}),
        (b"", {"Content-Type": "image/jpeg"}),
    ],
)
def test_fetch_image_refuses_anything_but_a_small_image(body, headers):
    with pytest.raises(ValueError):
        converter._fetch_image(ImageYDL(FakeResponse(body, headers)), "https://cdn.test/a", None)


# --- the thumbnail route ---


@pytest.mark.parametrize(
    "name",
    [
        f"tiktok-{VIDEO_ID}",  # valid but not stored
        f"tiktok-{VIDEO_ID}.jpg",
        "media-convert.db",
        "..%2Fmedia-convert.db",
        "%2E%2E%2Fmedia-convert.db",
        "..%5Cmedia-convert.db",
        "tiktok-..%2F..%2Fsecret",
        "tiktok-123%00.jpg",
        "youtube-abc",
        "*",
    ],
)
def test_thumbnail_route_serves_nothing_else(client, tmp_path, name):
    (tmp_path / "secret").write_text("secret")
    response = client.get(f"/api/thumbnails/{name}")
    assert response.status_code == 404
    assert b"secret" not in response.content


def test_thumbnail_route_rejects_traversal_in_the_path_itself(client, tmp_path):
    (tmp_path / "thumbs").mkdir()
    thumbnails.save(tmp_path / "thumbs", "tiktok-1", JPEG, "image/png")
    assert client.get("/api/thumbnails/tiktok-1").headers["content-type"] == "image/png"
    for path in ("/api/thumbnails/../media-convert.db", "/api/thumbnails/", "/api/thumbnails/tiktok-1/x"):
        assert client.get(path).status_code == 404


def test_saving_a_thumbnail_refuses_bad_names(tmp_path):
    for name in ("../x", "tiktok-1/../../x", "tiktok-", "tiktok-1.jpg"):
        with pytest.raises(ValueError):
            thumbnails.save(tmp_path, name, JPEG, "image/jpeg")
    assert list(tmp_path.iterdir()) == []


# --- jobs ---


def test_jobs_report_their_source(client, fake_convert):
    tiktok = client.post("/api/jobs", json={"url": "https://vm.tiktok.com/ZTR45GpSF", "format": "mp3"})
    youtube = client.post("/api/jobs", json={"url": "https://youtu.be/abc123", "format": "mp3"})
    assert tiktok.status_code == youtube.status_code == 201
    assert tiktok.json()["source"] == "tiktok"
    assert tiktok.json()["url"] == "https://vm.tiktok.com/ZTR45GpSF/"
    assert youtube.json()["source"] == "youtube"

    wait_for_job(client, tiktok.json()["id"])
    wait_for_job(client, youtube.json()["id"])
    sources = {job["id"]: job["source"] for job in client.get("/api/jobs").json()}
    assert sources == {tiktok.json()["id"]: "tiktok", youtube.json()["id"]: "youtube"}


def test_a_job_with_an_unknown_link_counts_as_youtube():
    row = {
        "id": "a", "url": "not a link", "format": "mp3", "quality": "192", "status": "queued",
        "stage": None, "progress": 0.0, "error": None, "video_id": None, "title": None,
        "thumbnail": None, "duration": None, "filesize": None, "created_at": "t",
        "started_at": None, "finished_at": None, "batch_id": None, "position": None,
    }
    assert Job.from_row(row, 24).source == "youtube"


def test_tiktok_links_cannot_start_a_playlist_conversion(client, fake_convert):
    body = {
        "url": URL,
        "title": "Not a playlist",
        "format": "mp3",
        "entries": [{"video_id": "aaaaaaaaaaa"}],
    }
    response = client.post("/api/batches", json=body)
    assert response.status_code == 422
    assert "YouTube playlists" in response.json()["detail"]
    assert fake_convert == []


def test_tiktok_conversion_uses_tiktok_formats_and_stores_the_thumbnail(data_dir, fake_lookup, monkeypatch):
    used = {}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def extract_info(self, url, download):
            return tiktok_info()

        def process_ie_result(self, info, download):
            (data_dir / "files" / "job1.mp3").write_bytes(b"audio")

    @contextmanager
    def youtube_dl(extra=None):
        used.update(extra or {})
        yield FakeYDL(extra)

    monkeypatch.setattr(converter, "_youtube_dl", youtube_dl)
    monkeypatch.setattr(converter, "_extract", lambda ydl, url, on_retry=None: ydl.extract_info(url, False))
    infos = []
    path = converter.convert(
        URL, "mp3", "192", data_dir / "files", "job1", infos.append, lambda *a: None
    )
    assert path.name == "job1.mp3"
    assert used["format"] == converter.TIKTOK.audio_selector
    assert infos[0]["source"] == "tiktok"
    assert infos[0]["thumbnail"] == THUMB_PATH
    assert (data_dir / "thumbs" / f"tiktok-{VIDEO_ID}.jpg").is_file()


# --- clean-up ---


def test_cleanup_removes_only_old_thumbnails_no_job_shows(data_dir):
    # A database of its own: the app's clean-up loop would race this test.
    settings = get_settings()
    db = Database(settings.db_path)
    db.init()
    folder = settings.thumbs_dir
    for name in ("tiktok-1", "tiktok-2", "tiktok-3"):
        thumbnails.save(folder, name, JPEG, "image/jpeg")
    old = time.time() - ORPHAN_AGE - 60
    os.utime(folder / "tiktok-1.jpg", (old, old))  # old and shown by a job: kept
    os.utime(folder / "tiktok-2.jpg", (old, old))  # old and unused: removed
    # tiktok-3 is recent and unused: kept, it may be about to be converted.

    job = db.create_job("https://www.tiktok.com/@a/video/1", "mp3", "192")
    db.update_job(job["id"], thumbnail="/api/thumbnails/tiktok-1")

    assert run_cleanup(db, settings)["thumbnails"] == 1
    assert sorted(path.name for path in folder.iterdir()) == ["tiktok-1.jpg", "tiktok-3.jpg"]
