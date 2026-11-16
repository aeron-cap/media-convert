"""Deleting, cancelling and automatic clean-up of conversions."""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app import converter
from app.cleanup import ORPHAN_AGE, run_cleanup
from app.config import get_settings

from .conftest import wait_for_job

URL = "https://www.youtube.com/watch?v=abc123"


def files(client):
    return sorted(p.name for p in get_settings().files_dir.iterdir())


def finish_job(client, format="mp3"):
    created = client.post("/api/jobs", json={"url": URL, "format": format}).json()
    return wait_for_job(client, created["id"])


def test_deleting_a_finished_job_removes_its_file(client, fake_convert):
    job = finish_job(client)
    assert files(client) == [f"{job['id']}.mp3"]

    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204

    assert files(client) == []
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert client.get("/api/jobs").json() == []
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 404


def test_deleting_a_running_job_cancels_it(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    outcome = []

    def convert(url, format, quality, out_dir, job_id, on_info, on_progress):
        (out_dir / f"{job_id}.part").write_bytes(b"partial")
        started.set()
        release.wait(5)
        try:
            on_progress("downloading", 0.5)  # raises once the job is cancelled
        except BaseException as error:
            outcome.append(type(error).__name__)
            raise
        outcome.append("finished")
        return out_dir / f"{job_id}.{format}"

    monkeypatch.setattr(converter, "convert", convert)
    created = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()
    assert started.wait(5)

    assert client.delete(f"/api/jobs/{created['id']}").status_code == 204
    assert client.get(f"/api/jobs/{created['id']}").status_code == 404
    release.set()

    deadline = time.monotonic() + 5
    while files(client) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert outcome == ["JobCancelled"]
    assert files(client) == []


def test_clear_removes_finished_jobs_only(client, monkeypatch, fake_convert):
    done = finish_job(client)

    def failing(*args, **kwargs):
        raise converter.ConversionError("Video unavailable")

    monkeypatch.setattr(converter, "convert", failing)
    failed = finish_job(client)
    assert failed["status"] == "failed"

    hold = threading.Event()
    monkeypatch.setattr(converter, "convert", lambda *a, **k: hold.wait(5) and None)
    active = client.post("/api/jobs", json={"url": URL, "format": "mp3"}).json()

    assert client.delete("/api/jobs").json() == {"deleted": 2}
    assert [job["id"] for job in client.get("/api/jobs").json()] == [active["id"]]
    assert files(client) == []
    assert done["id"] != active["id"]

    client.delete(f"/api/jobs/{active['id']}")
    hold.set()


def test_job_reports_when_it_expires(client, fake_convert, monkeypatch):
    job = finish_job(client)
    finished = datetime.fromisoformat(job["finished_at"])
    assert datetime.fromisoformat(job["expires_at"]) == finished + timedelta(hours=24)

    monkeypatch.setenv("RETENTION_HOURS", "0")
    get_settings.cache_clear()
    assert client.get(f"/api/jobs/{job['id']}").json()["expires_at"] is None


def test_cleanup_removes_expired_jobs_and_keeps_recent_ones(client, fake_convert):
    old = finish_job(client)
    recent = finish_job(client)
    db, settings = client.app.state.db, get_settings()
    long_ago = datetime.now(timezone.utc) - timedelta(hours=settings.retention_hours + 1)
    db.update_job(old["id"], finished_at=long_ago.isoformat(timespec="seconds"))

    assert run_cleanup(db, settings) == {"expired": 1, "orphans": 0, "thumbnails": 0}

    assert [job["id"] for job in client.get("/api/jobs").json()] == [recent["id"]]
    assert files(client) == [f"{recent['id']}.mp3"]


def test_cleanup_keeps_everything_when_retention_is_off(client, fake_convert, monkeypatch):
    job = finish_job(client)
    db = client.app.state.db
    db.update_job(job["id"], finished_at="2000-01-01T00:00:00+00:00")
    monkeypatch.setenv("RETENTION_HOURS", "0")
    get_settings.cache_clear()

    assert run_cleanup(db, get_settings()) == {"expired": 0, "orphans": 0, "thumbnails": 0}
    assert len(client.get("/api/jobs").json()) == 1


def test_cleanup_removes_only_old_files_without_a_job(client, fake_convert):
    job = finish_job(client)
    settings = get_settings()
    stale = settings.files_dir / "0123abcd.mp4"
    fresh = settings.files_dir / "4567cdef.part"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = time.time() - ORPHAN_AGE - 60
    os.utime(stale, (old, old))
    os.utime(settings.files_dir / f"{job['id']}.mp3", (old, old))

    assert run_cleanup(client.app.state.db, settings) == {"expired": 0, "orphans": 1, "thumbnails": 0}
    assert files(client) == sorted([f"{job['id']}.mp3", fresh.name])
