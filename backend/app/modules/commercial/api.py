"""Router of Inteligencia Comercial, mounted at /api/v1/commercial.

Every endpoint added here must depend on `require_module_access("commercial", ...)`.
"""

from fastapi import APIRouter

router = APIRouter(tags=["commercial"])
