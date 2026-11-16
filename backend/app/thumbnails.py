"""Thumbnails kept on this server, for sites whose own image links expire.

TikTok's thumbnail links are signed and stop working after a while, and the
page's content security policy only allows images from the app itself and
YouTube's image host. So a TikTok thumbnail is fetched once (by the converter)
and served from `/api/thumbnails/<name>`.
"""

import os
import re
import time
from pathlib import Path

# Largest thumbnail kept; bigger images are not stored.
MAX_THUMBNAIL_BYTES = 512 * 1024
# The image types that are stored, with the file extension each is kept under.
IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}
PATH_PREFIX = "/api/thumbnails/"
# "<site>-<video id>". Only names of this shape are ever used as file names.
_NAME = re.compile(r"tiktok-[0-9]{1,32}")


def name_for(source: str, video_id: str | None) -> str | None:
    """The stored thumbnail's name for a video, or None if the id looks wrong."""
    name = f"{source}-{video_id}"
    return name if is_valid_name(name) else None


def is_valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name))


def app_path(name: str) -> str:
    return PATH_PREFIX + name


def name_of_path(value: str | None) -> str | None:
    """The stored thumbnail a job's `thumbnail` value refers to, if any."""
    if value and value.startswith(PATH_PREFIX):
        name = value[len(PATH_PREFIX):]
        if is_valid_name(name):
            return name
    return None


def find(folder: Path, name: str) -> tuple[Path, str] | None:
    """The stored file and its content type, or None."""
    if not is_valid_name(name):
        return None
    for content_type, extension in IMAGE_TYPES.items():
        path = folder / f"{name}.{extension}"
        if path.is_file():
            return path, content_type
    return None


def touch(folder: Path, name: str) -> bool:
    """Mark a stored thumbnail as just used; False if there is none."""
    found = find(folder, name)
    if found is None:
        return False
    try:
        os.utime(found[0])
    except OSError:
        return False
    return True


def save(folder: Path, name: str, data: bytes, content_type: str) -> str:
    """Store an image and return the app path it is served at."""
    if not is_valid_name(name):
        raise ValueError(f"invalid thumbnail name: {name!r}")
    extension = IMAGE_TYPES[content_type]
    folder.mkdir(parents=True, exist_ok=True)
    for other in IMAGE_TYPES.values():
        if other != extension:
            (folder / f"{name}.{other}").unlink(missing_ok=True)
    target = folder / f"{name}.{extension}"
    partial = folder / f"{name}.{extension}.part"
    partial.write_bytes(data)
    partial.replace(target)
    return app_path(name)


def remove_unused(folder: Path, used: set[str], max_age: float) -> int:
    """Delete stored thumbnails not in `used` that are older than `max_age` seconds."""
    if not folder.is_dir():
        return 0
    oldest_kept = time.time() - max_age
    removed = 0
    for path in folder.iterdir():
        name = path.name.split(".", 1)[0]
        if path.is_file() and name not in used and path.stat().st_mtime < oldest_kept:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
