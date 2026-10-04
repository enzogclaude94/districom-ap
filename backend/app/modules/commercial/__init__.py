"""Inteligencia Comercial: commercial analytics over AXUM and STORE SYS (ROADMAP.md)."""

from app.modules.commercial.api import router
from app.modules.commercial.settings import SETTINGS
from app.platform.modules import ModuleSpec

MODULE = ModuleSpec(key="commercial", name="Inteligencia Comercial", router=router, settings=SETTINGS)
