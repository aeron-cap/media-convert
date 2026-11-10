from fastapi import APIRouter

from . import health

router = APIRouter(prefix="/api")
router.include_router(health.router)
