"""Who reaches Rendiciones > Integración Store Sys (`settlements.store_sys_import`, ADR 011, Enzo's R9-1 adjustment 3).

A protected area of Rendiciones with its own grant: the platform admin does not inherit it, and without it every
endpoint answers 404.

| Action | viewer | operator | admin |
|---|---|---|---|
| Read the employees' Store Sys codes and their history | yes | yes | yes |
| Send Store Sys records (also needs operator of Rendiciones) | - | yes | yes |
| Load, correct or remove an employee's Store Sys code | - | - | yes |

Accepting or keeping a pending Store Sys update is a decision on the settlement and belongs to Rendiciones roles
(`expected/router.py`), not to this area.
"""

from typing import Annotated

from fastapi import Depends

from app.platform.auth.access import require_area_access, require_module_access
from app.platform.auth.models import User
from app.platform.modules import ProtectedAreaSpec

AREA = "settlements.store_sys_import"
AREA_SPEC = ProtectedAreaSpec(AREA, "Integración Store Sys", explicit_grant_required=True)

AreaViewer = Annotated[User, Depends(require_area_access(AREA, "viewer"))]
AreaAdmin = Annotated[User, Depends(require_area_access(AREA, "admin"))]
_area_operator = require_area_access(AREA, "operator")
_module_operator = require_module_access("settlements", "operator")


def _importer(user: Annotated[User, Depends(_area_operator)]) -> User:
    """Operator of the area (404 without it) and operator of Rendiciones."""
    return _module_operator(user)


Importer = Annotated[User, Depends(_importer)]
