from fastapi import APIRouter

from . import batches, health, jobs

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(jobs.router)
router.include_router(batches.router)
