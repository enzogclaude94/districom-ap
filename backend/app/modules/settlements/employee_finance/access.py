"""Who reaches Rendiciones > Empleados (`settlements.employee_finance`, ADR 010, Enzo's D1 and D12).

A protected area of Rendiciones with its own grant: a role in Rendiciones is not enough, and the platform
admin does not inherit it. Without the area every endpoint answers 404 (nothing to discover).

| Action | viewer | operator | admin |
|---|---|---|---|
| Read internal accounts, salary terms and movements | yes | yes | yes |
| Register a consumption (R8-2) | - | yes | yes |
| Hand over an advance, register an adjustment (R8-2) | - | - | yes |
| Reverse a movement (R8-2) | - | - | yes |
| Create or end a salary term | - | - | yes |

The ledger checks the same roles again (`ledger.rules`: the area authorizes the R8 operations).
"""

from typing import Annotated

from fastapi import Depends

from app.modules.settlements.ledger.rules import EMPLOYEE_FINANCE_AREA
from app.platform.auth.access import has_area_access, require_area_access
from app.platform.auth.models import User
from app.platform.modules import ProtectedAreaSpec

AREA = EMPLOYEE_FINANCE_AREA
AREA_SPEC = ProtectedAreaSpec(AREA, "Empleados", explicit_grant_required=True)

AreaViewer = Annotated[User, Depends(require_area_access(AREA, "viewer"))]
AreaOperator = Annotated[User, Depends(require_area_access(AREA, "operator"))]
AreaAdmin = Annotated[User, Depends(require_area_access(AREA, "admin"))]


def can_read(user: User) -> bool:
    """Whether the user may see employee finance anywhere (Cajas included)."""
    return has_area_access(user, AREA)
