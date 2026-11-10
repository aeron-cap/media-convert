from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Media Converter", lifespan=lifespan)
    app.include_router(api_router)

    # Serve the built frontend when it exists (it does in the Docker image;
    # in local development Vite serves it and proxies /api here instead).
    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

    return app


app = create_app()
