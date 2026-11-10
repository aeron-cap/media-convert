import shutil

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }
