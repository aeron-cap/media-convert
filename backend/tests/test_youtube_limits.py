"""The measures that keep the app under YouTube's bot checks."""

import pytest
from yt_dlp.utils import DownloadError

from app import converter
from app.config import get_settings

URL = "https://www.youtube.com/watch?v=abc123"
BOT_CHECK = "ERROR: [youtube] abc123: Sign in to confirm you’re not a bot."


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    """Set settings through the environment; returns a function to do so."""
    for name in ("YT_COOKIES_FILE", "YT_PROXY", "YT_POT_PROVIDER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YT_MIN_INTERVAL_SECONDS", "0")

    def apply(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, str(value))
        get_settings.cache_clear()

    apply()
    yield apply
    get_settings.cache_clear()


@pytest.fixture
def sleeps(monkeypatch):
    """Record sleeps instead of waiting."""
    recorded = []
    monkeypatch.setattr(converter.time, "sleep", recorded.append)
    return recorded


class FakeYDL:
    """Stands in for YoutubeDL.extract_info: fails `failures` times, then works."""

    def __init__(self, failures=0, error=BOT_CHECK):
        self.failures = failures
        self.error = error
        self.calls = 0

    def extract_info(self, url, download):
        self.calls += 1
        if self.calls <= self.failures:
            raise DownloadError(self.error)
        return {"id": "abc123"}


# --- 4.1 video details cache ---


def test_probing_the_same_link_twice_asks_youtube_once(settings_env, monkeypatch):
    fetched = []

    def extract(ydl, url, on_retry=None):
        fetched.append(url)
        return {"id": "abc123", "title": "A video", "duration": 12.0, "formats": []}

    monkeypatch.setattr(converter, "_extract", extract)
    converter._info_cache.clear()

    first = converter.probe(URL)
    first["title"] = "changed by the caller"
    second = converter.probe(URL)

    assert fetched == [URL]
    assert second["title"] == "A video"
    converter._info_cache.clear()


def test_cache_returns_a_private_copy():
    cache = converter._InfoCache()
    cache.put(URL, {"id": "abc123", "formats": [{"format_id": "18"}]})

    first = cache.take(URL)
    first["formats"].clear()

    assert cache.take(URL) == {"id": "abc123", "formats": [{"format_id": "18"}]}
    assert cache.take("https://youtu.be/other") is None


def test_cache_entries_expire(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(converter.time, "monotonic", lambda: clock[0])
    cache = converter._InfoCache()
    cache.put(URL, {"id": "abc123"})

    clock[0] += converter.INFO_CACHE_SECONDS - 1
    assert cache.take(URL) is not None
    clock[0] += 2
    assert cache.take(URL) is None


def test_cache_is_bounded():
    cache = converter._InfoCache()
    for n in range(converter.INFO_CACHE_SIZE + 5):
        cache.put(f"{URL}{n}", {"id": n})

    assert cache.take(f"{URL}0") is None
    assert cache.take(f"{URL}{converter.INFO_CACHE_SIZE + 4}") is not None


# --- 4.2 pacing ---


def test_throttle_spaces_calls_apart(monkeypatch, sleeps):
    clock = [50.0]
    monkeypatch.setattr(converter.time, "monotonic", lambda: clock[0])
    throttle = converter._Throttle()

    throttle.wait(2.0)  # first call goes straight through
    throttle.wait(2.0)  # same instant: must wait a full interval
    clock[0] += 3.5
    throttle.wait(2.0)  # 0.5s left of the second call's interval

    assert sleeps == [2.0, 0.5]


def test_throttle_disabled_with_zero_interval(sleeps):
    throttle = converter._Throttle()
    throttle.wait(0)
    throttle.wait(0)
    assert sleeps == []


def test_request_sleep_is_passed_to_yt_dlp(settings_env):
    settings_env(YT_REQUEST_SLEEP_SECONDS="0.75")
    assert converter._base_opts()["sleep_interval_requests"] == 0.75

    settings_env(YT_REQUEST_SLEEP_SECONDS="0")
    assert "sleep_interval_requests" not in converter._base_opts()


# --- 4.3 automatic retry ---


def test_bot_check_is_retried_until_it_passes(settings_env, sleeps):
    settings_env(YT_RETRIES="2")
    ydl = FakeYDL(failures=2)
    retries = []

    assert converter._extract(ydl, URL, on_retry=retries.append) == {"id": "abc123"}
    assert ydl.calls == 3
    assert retries == [1, 2]
    assert sleeps == [4, 10]


def test_bot_check_gives_up_after_the_configured_retries(settings_env, sleeps):
    settings_env(YT_RETRIES="1")
    ydl = FakeYDL(failures=5)

    with pytest.raises(DownloadError):
        converter._extract(ydl, URL)
    assert ydl.calls == 2


def test_other_errors_are_not_retried(settings_env, sleeps):
    ydl = FakeYDL(failures=5, error="ERROR: [youtube] abc123: Video unavailable")

    with pytest.raises(DownloadError):
        converter._extract(ydl, URL)
    assert ydl.calls == 1
    assert sleeps == []


# --- 4.4 cookies, 4.5 proxy, 4.6 PO token provider ---


def test_optional_measures_are_off_by_default(settings_env):
    opts = converter._base_opts()
    assert "proxy" not in opts
    assert "extractor_args" not in opts
    with converter._youtube_dl() as ydl:
        assert not ydl.params.get("cookiefile")


def test_cookies_are_used_from_a_private_copy(settings_env, tmp_path):
    original = tmp_path / "cookies.txt"
    original.write_text("# Netscape HTTP Cookie File\n")
    settings_env(YT_COOKIES_FILE=original)

    with converter._youtube_dl() as ydl:
        working_copy = ydl.params["cookiefile"]
        assert working_copy != str(original)
        assert open(working_copy).read() == original.read_text()

    import os

    assert not os.path.exists(working_copy)
    assert original.read_text() == "# Netscape HTTP Cookie File\n"


def test_missing_cookies_file_is_ignored(settings_env, tmp_path):
    settings_env(YT_COOKIES_FILE=tmp_path / "missing.txt")
    with converter._youtube_dl() as ydl:
        assert not ydl.params.get("cookiefile")


def test_proxy_and_pot_provider_are_passed_to_yt_dlp(settings_env):
    settings_env(YT_PROXY="socks5://10.0.0.2:1080", YT_POT_PROVIDER_URL="http://pot-provider:4416")
    opts = converter._base_opts()

    assert opts["proxy"] == "socks5://10.0.0.2:1080"
    assert opts["extractor_args"] == {
        "youtubepot-bgutilhttp": {"base_url": ["http://pot-provider:4416"]},
        "youtube": {"player_client": ["default", "mweb"]},
    }
