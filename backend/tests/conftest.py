import time

import pytest
from fastapi.testclient import TestClient

from app import converter
from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app with its own empty data directory and no frontend."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-static"))
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def fake_convert(monkeypatch):
    """Replace the real download/convert step with one that writes a small file."""
    calls = []

    def convert(url, format, quality, out_dir, job_id, on_info, on_progress):
        calls.append((url, format, quality))
        on_info(
            {
                "video_id": "abc123",
                "title": 'My: "Video" / Title?',
                "thumbnail": "https://example.test/thumb.jpg",
                "duration": 61,
                "uploader": "Someone",
                "max_height": 720,
            }
        )
        on_progress("downloading", 0.5)
        on_progress("converting", 1.0)
        path = out_dir / f"{job_id}.{format}"
        path.write_bytes(b"fake media")
        return path

    monkeypatch.setattr(converter, "convert", convert)
    return calls


def wait_for_job(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {job}")
