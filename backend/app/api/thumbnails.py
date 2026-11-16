"""Thumbnails stored on this server (TikTok's own links expire)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import thumbnails
from ..config import get_settings

router = APIRouter()

# A video's thumbnail doesn't change; let the browser keep it for a day.
CACHE_CONTROL = "private, max-age=86400"


@router.get("/thumbnails/{name}")
def get_thumbnail(name: str) -> FileResponse:
    # find() only accepts names of one strict shape ("tiktok-<digits>"), so
    # nothing from the request reaches the file system otherwise.
    found = thumbnails.find(get_settings().thumbs_dir, name)
    if found is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={"Cache-Control": CACHE_CONTROL})
