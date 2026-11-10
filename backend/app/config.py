import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    max_concurrent_jobs: int
    retention_hours: int
    max_duration_minutes: int
    max_playlist_items: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "media-convert.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "./data")),
        static_dir=Path(os.environ.get("STATIC_DIR", "../frontend/dist")),
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 2),
        retention_hours=_int("RETENTION_HOURS", 24),
        max_duration_minutes=_int("MAX_DURATION_MINUTES", 180),
        max_playlist_items=_int("MAX_PLAYLIST_ITEMS", 50),
    )
