import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .cleanup import cleanup_loop
from .config import get_settings
from .db import Database
from .worker import Worker


# Sent with every response. The page loads nothing from other sites except
# video thumbnails, and nothing may embed it in a frame.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https://i.ytimg.com https://*.ytimg.com data:; "
        "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    ),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.files_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    db.init()
    worker = Worker(db, settings)
    worker.start()
    app.state.db = db
    app.state.worker = worker
    cleanup = asyncio.create_task(cleanup_loop(db, settings))

    yield

    cleanup.cancel()
    worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Media Converter", lifespan=lifespan)
    app.include_router(api_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # FastAPI's own /docs page loads its scripts from a CDN; leave it usable.
        relaxed = request.url.path in ("/docs", "/redoc")
        for name, value in SECURITY_HEADERS.items():
            if not (relaxed and name == "Content-Security-Policy"):
                response.headers.setdefault(name, value)
        return response

    # Serve the built frontend when it exists (it does in the Docker image;
    # in local development Vite serves it and proxies /api here instead).
    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

    return app


app = create_app()
