from fastapi import APIRouter

from . import batches, health, jobs, thumbnails

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(jobs.router)
router.include_router(batches.router)
router.include_router(thumbnails.router)
