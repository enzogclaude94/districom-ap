"""Inteligencia Comercial: commercial analytics over AXUM and STORE SYS (ROADMAP.md)."""

from app.modules.commercial.api import router
from app.modules.commercial.settings import SETTINGS
from app.platform.modules import ModuleSpec
from app.platform.parties.functions import EmployeeFunctionSpec
from app.platform.quality.registry import QualityCodeSpec

MODULE = ModuleSpec(
    key="commercial",
    name="Inteligencia Comercial",
    router=router,
    settings=SETTINGS,
    employee_functions=(EmployeeFunctionSpec("commercial.salesperson", "Preventista"),),
    quality_codes=(
        QualityCodeSpec("commercial.zone.unconfigured", "warning", "Zona sin configurar"),
        QualityCodeSpec("commercial.document.unclassified_type", "warning", "Tipo de comprobante sin clasificar"),
        QualityCodeSpec("commercial.product.without_category", "warning", "Producto sin categoría"),
    ),
)
