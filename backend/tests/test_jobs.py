import json

from app import converter
from app.converter import ConversionError

from .conftest import wait_for_job

URL = "https://www.youtube.com/watch?v=abc123"


def test_job_runs_to_done_and_file_downloads(client, fake_convert):
    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"})
    assert created.status_code == 201
    assert created.json()["status"] == "queued"
    assert created.json()["download_url"] is None

    job = wait_for_job(client, created.json()["id"])
    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["title"] == 'My: "Video" / Title?'
    assert job["duration"] == 61
    assert job["filesize"] == len(b"fake media")
    assert fake_convert == [(URL, "mp3")]

    download = client.get(job["download_url"])
    assert download.status_code == 200
    assert download.content == b"fake media"
    # Characters that are illegal in filenames are stripped from the title.
    assert "My%20Video%20Title.mp3" in download.headers["content-disposition"]


def test_jobs_are_listed_newest_first(client, fake_convert):
    first = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()
    second = client.post("/api/jobs", json={"url": URL, "format": "mp4"}).json()

    listed = client.get("/api/jobs").json()
    assert [job["id"] for job in listed] == [second["id"], first["id"]]


def test_conversion_error_is_shown_on_the_job(client, monkeypatch):
    def convert(*args, **kwargs):
        raise ConversionError("Video unavailable")

    monkeypatch.setattr(converter, "convert", convert)
    created = client.post("/api/jobs", json={"url": URL, "format": "mp4"}).json()

    job = wait_for_job(client, created["id"])
    assert job["status"] == "failed"
    assert job["error"] == "Video unavailable"
    assert client.get(f"/api/jobs/{job['id']}/file").status_code == 409


def test_unexpected_error_is_not_leaked(client, monkeypatch):
    def convert(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(converter, "convert", convert)
    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()

    job = wait_for_job(client, created["id"])
    assert job["status"] == "failed"
    assert "secret" not in job["error"]


def test_video_over_duration_limit_fails(client, fake_convert, monkeypatch):
    monkeypatch.setenv("MAX_DURATION_MINUTES", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    client.app.state.worker.settings = get_settings()

    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()
    job = wait_for_job(client, created["id"])
    assert job["status"] == "failed"
    assert "1-minute limit" in job["error"]


def test_rejects_non_youtube_url(client, fake_convert):
    response = client.post("/api/jobs", json={"url": "https://example.com/video", "format": "mp3"})
    assert response.status_code == 422
    assert fake_convert == []


def test_rejects_unknown_format(client, fake_convert):
    response = client.post("/api/jobs", json={"url": URL, "format": "exe"})
    assert response.status_code == 422


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/file").status_code == 404


def test_probe_returns_video_info(client, monkeypatch):
    info = {
        "video_id": "abc123",
        "title": "A video",
        "thumbnail": None,
        "duration": 10,
        "uploader": "Someone",
    }
    monkeypatch.setattr(converter, "probe", lambda url: info)

    response = client.post("/api/probe", json={"url": URL})
    assert response.status_code == 200
    assert response.json() == info


def test_probe_rejects_non_youtube_url(client):
    response = client.post("/api/probe", json={"url": "https://example.com/video"})
    assert response.status_code == 422
    assert "YouTube" in response.json()["detail"]


def test_events_stream_ends_with_the_finished_job(client, fake_convert):
    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()
    wait_for_job(client, created["id"])

    response = client.get(f"/api/jobs/{created['id']}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(events) == 1
    job = json.loads(events[0].removeprefix("data: "))
    assert job["status"] == "done"
    assert job["download_url"] == f"/api/jobs/{created['id']}/file"


def test_events_for_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope/events").status_code == 404
