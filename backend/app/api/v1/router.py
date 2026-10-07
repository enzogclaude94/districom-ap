"""Composition root: the only place that knows every module (ARCHITECTURE.md §4.2)."""

from fastapi import APIRouter

from app.api.v1 import health
from app.modules import MODULES
from app.platform.auth import router as auth
from app.platform.banks import router as banks
from app.platform.modules import install
from app.platform.parties import router as employees
from app.platform.settings import router as settings

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(settings.router)
api_router.include_router(employees.router)
api_router.include_router(banks.router)

for module in MODULES:
    install(module)
    api_router.include_router(module.router, prefix=f"/{module.key}")
