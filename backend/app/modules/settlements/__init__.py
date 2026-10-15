"""Rendiciones: settlements of deliveries and the counter, cash, cheques and payroll (ADR 007, ROADMAP.md)."""

from app.modules.settlements.api import router
from app.modules.settlements.employee_finance.access import AREA_SPEC
from app.modules.settlements.settings import SETTINGS
from app.platform.modules import ModuleSpec
from app.platform.parties.functions import EmployeeFunctionSpec

MODULE = ModuleSpec(
    key="settlements",
    name="Rendiciones",
    router=router,
    settings=SETTINGS,
    employee_functions=(EmployeeFunctionSpec("settlements.delivery_driver", "Repartidor"),),
    # Rendiciones > Empleados (R8, ADR 010): its own grant, not inherited by the platform admin.
    protected_areas=(AREA_SPEC,),
)
