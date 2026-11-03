"""Router of Inteligencia Comercial, mounted at /api/v1/commercial.

Every endpoint added here must depend on `require_module_access("commercial", ...)`.
"""

from fastapi import APIRouter

from app.modules.commercial.catalog.router import router as catalog_router
from app.modules.commercial.config.router import router as config_router
from app.modules.commercial.identities.router import router as identities_router

router = APIRouter(tags=["commercial"])
router.include_router(identities_router)
router.include_router(config_router)
router.include_router(catalog_router)
