"""Changing the quality of conversions that are still waiting to start."""

import threading

import pytest

from app import converter

from .conftest import wait_for_job

URL = "https://www.youtube.com/watch?v=abc123"
ENTRIES = [{"video_id": c * 11, "title": c.upper()} for c in "abcd"]


@pytest.fixture
def one_at_a_time(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")


@pytest.fixture
def held_convert(monkeypatch):
    """A converter that waits for the test, and records what it was asked for."""
    release = threading.Event()
    calls = []

    def convert(url, format, quality, out_dir, job_id, on_info, on_progress):
        calls.append((url, format, quality))
        release.wait(5)
        path = out_dir / f"{job_id}.{format}"
        path.write_bytes(b"fake media")
        return path

    monkeypatch.setattr(converter, "convert", convert)
    yield release, calls
    release.set()


def start(client, format="mp4", quality="best"):
    created = client.post("/api/jobs", json={"url": URL, "format": format, "quality": quality})
    assert created.status_code == 201
    return created.json()


def wait_until_running(client, job_id):
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/api/jobs/{job_id}").json()["status"] != "queued":
            return
        time.sleep(0.01)
    raise AssertionError("job never started")


def test_waiting_job_converts_at_its_new_quality(one_at_a_time, client, held_convert):
    release, calls = held_convert
    first = start(client)
    wait_until_running(client, first["id"])
    second = start(client)

    changed = client.patch(f"/api/jobs/{second['id']}", json={"quality": "720"})
    assert changed.status_code == 200
    assert changed.json()["quality"] == "720"
    assert changed.json()["status"] == "queued"

    release.set()
    wait_for_job(client, first["id"])
    assert wait_for_job(client, second["id"])["quality"] == "720"
    assert calls == [(URL, "mp4", "best"), (URL, "mp4", "720")]


def test_started_job_cannot_be_changed(one_at_a_time, client, held_convert):
    release, calls = held_convert
    job = start(client)
    wait_until_running(client, job["id"])

    refused = client.patch(f"/api/jobs/{job['id']}", json={"quality": "720"})
    assert refused.status_code == 409
    assert "already started" in refused.json()["detail"]

    release.set()
    done = wait_for_job(client, job["id"])
    assert done["quality"] == "best"
    assert client.patch(f"/api/jobs/{job['id']}", json={"quality": "720"}).status_code == 409


def test_quality_must_suit_the_format(one_at_a_time, client, held_convert):
    release, _ = held_convert
    first = start(client)
    wait_until_running(client, first["id"])
    video = start(client)
    lossless = start(client, format="flac", quality=None)

    assert client.patch(f"/api/jobs/{video['id']}", json={"quality": "320"}).status_code == 422
    assert client.patch(f"/api/jobs/{video['id']}", json={}).status_code == 422
    no_choice = client.patch(f"/api/jobs/{lossless['id']}", json={"quality": "320"})
    assert no_choice.status_code == 422
    assert "FLAC" in no_choice.json()["detail"]
    assert client.patch("/api/jobs/nope", json={"quality": "720"}).status_code == 404
    assert client.get(f"/api/jobs/{video['id']}").json()["quality"] == "best"

    release.set()
    for job in (first, video, lossless):
        wait_for_job(client, job["id"])


def test_playlist_change_applies_to_waiting_videos_only(one_at_a_time, client, held_convert):
    release, calls = held_convert
    body = {
        "url": "https://www.youtube.com/playlist?list=PLabc",
        "title": "Mix",
        "format": "mp3",
        "quality": "128",
        "entries": ENTRIES,
    }
    created = client.post("/api/batches", json=body).json()
    batch_id = created["batch"]["id"]
    wait_until_running(client, created["jobs"][0]["id"])

    changed = client.patch(f"/api/batches/{batch_id}", json={"quality": "320"})
    assert changed.status_code == 200
    assert changed.json()["batch"]["quality"] == "320"
    # The video that is already running keeps what it started with.
    assert [job["quality"] for job in changed.json()["jobs"]] == ["128", "320", "320", "320"]
    assert client.patch(f"/api/batches/{batch_id}", json={"quality": "720"}).status_code == 422
    assert client.patch("/api/batches/nope", json={"quality": "320"}).status_code == 404

    release.set()
    for job in created["jobs"]:
        wait_for_job(client, job["id"])
    assert [quality for _, _, quality in calls] == ["128", "320", "320", "320"]
    # Nothing is left waiting, so there is nothing to change.
    assert client.patch(f"/api/batches/{batch_id}", json={"quality": "192"}).status_code == 409
