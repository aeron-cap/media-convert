from fastapi import APIRouter

from . import health, jobs

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(jobs.router)
