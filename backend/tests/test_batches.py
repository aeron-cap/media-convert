"""Playlists: lookup, batch conversion and zip download."""

import io
import zipfile

from app import converter
from app.config import get_settings
from app.zipstream import unique_names, zip_stream

from .conftest import wait_for_job

PLAYLIST = "https://www.youtube.com/playlist?list=PLabc"
ENTRIES = [
    {"video_id": "aaaaaaaaaaa", "title": "First"},
    {"video_id": "bbbbbbbbbbb", "title": "Second"},
    {"video_id": "ccccccccccc", "title": "Third"},
]


def create_batch(client, entries=ENTRIES, **overrides):
    body = {"url": PLAYLIST, "title": "My: playlist", "format": "mp3", "entries": entries}
    return client.post("/api/batches", json={**body, **overrides})


def finish(client, created):
    return [wait_for_job(client, job["id"]) for job in created["jobs"]]


# --- recognising and listing playlists ---


def test_only_links_to_a_playlist_itself_count_as_playlists():
    assert converter.is_playlist_url(PLAYLIST)
    assert converter.is_playlist_url("https://music.youtube.com/playlist?list=PLabc")
    # A video opened from inside a playlist is still one video.
    assert not converter.is_playlist_url("https://www.youtube.com/watch?v=abc&list=PLabc")
    assert not converter.is_playlist_url("https://www.youtube.com/playlist")
    assert not converter.is_playlist_url("https://youtu.be/abc123")


def test_playlist_summary_skips_unavailable_videos_and_respects_the_limit():
    info = {
        "title": "Mix",
        "channel": "Someone",
        "playlist_count": 5,
        "entries": [
            {"id": "aaaaaaaaaaa", "title": "First", "duration": 61.0},
            {"id": "bbbbbbbbbbb", "title": "[Private video]"},
            {"id": "ccccccccccc", "title": "Third", "duration": None},
            None,
            {"id": "ddddddddddd", "title": "Fourth", "duration": 5},
        ],
    }

    summary = converter._playlist_summary(info, limit=2)

    assert summary["kind"] == "playlist"
    assert summary["title"] == "Mix"
    assert summary["uploader"] == "Someone"
    assert [e["video_id"] for e in summary["entries"]] == ["aaaaaaaaaaa", "ccccccccccc"]
    assert summary["entries"][0]["duration"] == 61
    assert summary["entries"][0]["thumbnail"].endswith("/aaaaaaaaaaa/mqdefault.jpg")
    assert summary["total"] == 5
    assert summary["truncated"] is True


def test_probe_returns_a_playlist(client, monkeypatch):
    listed = {
        "kind": "playlist",
        "title": "Mix",
        "uploader": None,
        "entries": [{"video_id": "aaaaaaaaaaa", "title": "First", "duration": 61, "thumbnail": None}],
        "total": 1,
        "truncated": False,
    }
    monkeypatch.setattr(converter, "probe", lambda url: listed)

    response = client.post("/api/probe", json={"url": PLAYLIST})
    assert response.status_code == 200
    assert response.json() == listed


# --- converting a playlist ---


def test_batch_creates_one_job_per_video_in_order(client, fake_convert):
    response = create_batch(client, quality="320")
    assert response.status_code == 201
    created = response.json()

    assert created["batch"]["title"] == "My: playlist"
    assert created["batch"]["zip_url"] == f"/api/batches/{created['batch']['id']}/zip"
    assert [job["position"] for job in created["jobs"]] == [1, 2, 3]
    assert [job["title"] for job in created["jobs"]] == ["First", "Second", "Third"]
    assert {job["batch_id"] for job in created["jobs"]} == {created["batch"]["id"]}
    assert {job["quality"] for job in created["jobs"]} == {"320"}

    jobs = finish(client, created)
    assert {job["status"] for job in jobs} == {"done"}
    assert sorted(call[0] for call in fake_convert) == [
        f"https://www.youtube.com/watch?v={entry['video_id']}" for entry in ENTRIES
    ]
    assert [batch["id"] for batch in client.get("/api/batches").json()] == [created["batch"]["id"]]


def test_batch_rejects_bad_input(client, fake_convert, monkeypatch):
    assert create_batch(client, entries=[]).status_code == 422
    assert create_batch(client, entries=[{"video_id": "../../etc"}]).status_code == 422
    assert create_batch(client, url="https://example.com/playlist?list=x").status_code == 422
    assert create_batch(client, quality="720").status_code == 422

    monkeypatch.setenv("MAX_PLAYLIST_ITEMS", "2")
    get_settings.cache_clear()
    too_many = create_batch(client)
    assert too_many.status_code == 422
    assert "at most 2" in too_many.json()["detail"]

    assert client.get("/api/jobs").json() == []
    assert fake_convert == []


def test_zip_contains_the_finished_files(client, fake_convert):
    created = create_batch(client).json()
    finish(client, created)

    response = client.get(created["batch"]["zip_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "My%20playlist.zip" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    # The fake converter titles every video the same; names stay distinct.
    assert archive.namelist() == [
        "01 - My Video Title.mp3",
        "02 - My Video Title.mp3",
        "03 - My Video Title.mp3",
    ]
    assert archive.read("02 - My Video Title.mp3") == b"fake media"
    assert archive.testzip() is None


def test_zip_needs_at_least_one_finished_file(client, monkeypatch):
    def failing(*args, **kwargs):
        raise converter.ConversionError("Video unavailable")

    monkeypatch.setattr(converter, "convert", failing)
    created = create_batch(client).json()
    finish(client, created)

    assert client.get(created["batch"]["zip_url"]).status_code == 409
    assert client.get("/api/batches/nope/zip").status_code == 404


def test_deleting_a_batch_removes_its_jobs_and_files(client, fake_convert):
    created = create_batch(client).json()
    finish(client, created)
    single = client.post("/api/jobs", json={"url": "https://youtu.be/abc123", "format": "mp3"}).json()
    wait_for_job(client, single["id"])

    assert client.delete(f"/api/batches/{created['batch']['id']}").status_code == 204

    assert [job["id"] for job in client.get("/api/jobs").json()] == [single["id"]]
    assert client.get("/api/batches").json() == []
    assert [p.name for p in get_settings().files_dir.iterdir()] == [f"{single['id']}.mp3"]
    assert client.delete(f"/api/batches/{created['batch']['id']}").status_code == 404


def test_batch_disappears_once_its_last_job_is_removed(client, fake_convert):
    created = create_batch(client, entries=ENTRIES[:1]).json()
    (job,) = finish(client, created)

    client.delete(f"/api/jobs/{job['id']}")

    assert client.get("/api/batches").json() == []


# --- zip helpers ---


def test_zip_stream_round_trips_large_and_empty_files(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"0123456789abcdef" * 200_000)  # 3.2 MB, several chunks
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")

    data = b"".join(zip_stream([("a/big.bin", big), ("empty.bin", empty)]))

    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.namelist() == ["a/big.bin", "empty.bin"]
    assert archive.read("a/big.bin") == big.read_bytes()
    assert archive.read("empty.bin") == b""
    assert archive.testzip() is None


def test_unique_names():
    assert unique_names(["a.mp3", "A.mp3", "b.mp3", "a.mp3", "noext", "noext"]) == [
        "a.mp3",
        "A (2).mp3",
        "b.mp3",
        "a (3).mp3",
        "noext",
        "noext (2)",
    ]
