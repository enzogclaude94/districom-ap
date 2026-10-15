"""Who reaches Rendiciones > Empleados (`settlements.employee_finance`, ADR 010, Enzo's D1 and D12).

A protected area of Rendiciones with its own grant: a role in Rendiciones is not enough, and the platform
admin does not inherit it. Without the area every endpoint answers 404 (nothing to discover).

| Action | viewer | operator | admin |
|---|---|---|---|
| Read internal accounts and salary terms | yes | yes | yes |
| Create or end a salary term | - | - | yes |
"""

from typing import Annotated

from fastapi import Depends

from app.platform.auth.access import has_area_access, require_area_access
from app.platform.auth.models import User
from app.platform.modules import ProtectedAreaSpec

AREA = "settlements.employee_finance"
AREA_SPEC = ProtectedAreaSpec(AREA, "Empleados", explicit_grant_required=True)

AreaViewer = Annotated[User, Depends(require_area_access(AREA, "viewer"))]
AreaAdmin = Annotated[User, Depends(require_area_access(AREA, "admin"))]


def can_read(user: User) -> bool:
    """Whether the user may see employee finance anywhere (Cajas included)."""
    return has_area_access(user, AREA)
