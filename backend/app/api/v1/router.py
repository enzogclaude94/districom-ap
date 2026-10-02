from fastapi import APIRouter

from app.api.v1 import health
from app.auth import router as auth
from app.settings_store import router as settings

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(settings.router)
