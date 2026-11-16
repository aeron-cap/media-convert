import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _text(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _int(name: str, default: int) -> int:
    raw = _text(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = _text(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    max_concurrent_jobs: int
    retention_hours: int
    max_duration_minutes: int
    max_playlist_items: int
    # Most conversions that may be waiting or running at once; more are refused.
    max_pending_jobs: int

    # --- Staying under YouTube's bot checks (they apply to TikTok too) ---
    # Minimum seconds between two metadata fetches, across all jobs and sites.
    yt_min_interval: float
    # Seconds yt-dlp waits between its own requests while fetching metadata.
    yt_request_sleep: float
    # How many times to retry when YouTube answers with its bot check.
    yt_retries: int
    # Netscape-format cookies file of a logged-in YouTube account (optional).
    yt_cookies_file: Path | None
    # Proxy for all YouTube traffic, e.g. socks5://10.0.0.2:1080 (optional).
    yt_proxy: str | None
    # Base URL of a bgutil PO token provider, e.g. http://pot-provider:4416 (optional).
    yt_pot_provider_url: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "media-convert.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def thumbs_dir(self) -> Path:
        """Thumbnails stored for sites whose own image links expire (TikTok)."""
        return self.data_dir / "thumbs"


@lru_cache
def get_settings() -> Settings:
    cookies = _text("YT_COOKIES_FILE")
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "./data")),
        static_dir=Path(os.environ.get("STATIC_DIR", "../frontend/dist")),
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 2),
        retention_hours=_int("RETENTION_HOURS", 24),
        max_duration_minutes=_int("MAX_DURATION_MINUTES", 180),
        max_playlist_items=_int("MAX_PLAYLIST_ITEMS", 50),
        max_pending_jobs=_int("MAX_PENDING_JOBS", 100),
        yt_min_interval=_float("YT_MIN_INTERVAL_SECONDS", 2.0),
        yt_request_sleep=_float("YT_REQUEST_SLEEP_SECONDS", 0.5),
        yt_retries=_int("YT_RETRIES", 2),
        yt_cookies_file=Path(cookies) if cookies else None,
        yt_proxy=_text("YT_PROXY"),
        yt_pot_provider_url=_text("YT_POT_PROVIDER_URL"),
    )
