import shutil

from fastapi import APIRouter

from ..config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    cookies = settings.yt_cookies_file
    return {
        "status": "ok",
        "ffmpeg": shutil.which("ffmpeg") is not None,
        # Which optional YouTube bot-check measures are switched on.
        "youtube": {
            "cookies": bool(cookies and cookies.is_file()),
            "proxy": settings.yt_proxy is not None,
            "pot_provider": settings.yt_pot_provider_url is not None,
        },
    }
