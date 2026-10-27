"""«Cierre del día» API (R9-2), mounted at /api/v1/settlements. Read-only, for any role in Rendiciones; employee
movements are reserved without `settlements.employee_finance` (ADR 010). Nothing here writes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.core.clock import business_today
from app.modules.settlements.daily_close import export, schemas, service
from app.modules.settlements.employee_finance.access import can_read as can_read_employee_finance
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: cierre del día"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Day = Annotated[date | None, Query(description="Fecha de negocio; hoy si se omite")]


def _view(db: DbSession, user: User, on: date | None) -> schemas.DailyCloseOut:
    return service.daily_close(db, on or business_today(), can_read_employee_finance=can_read_employee_finance(user))


@router.get("/daily-close", response_model=schemas.DailyCloseOut)
def daily_close(db: DbSession, user: Viewer, on: Day = None) -> schemas.DailyCloseOut:
    return _view(db, user, on)


@router.get(
    "/daily-close.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "El cierre del día en CSV (separador `;`)."}},
)
def daily_close_csv(db: DbSession, user: Viewer, on: Day = None) -> Response:
    view = _view(db, user, on)
    return Response(
        content=export.csv_text(view),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="cierre-del-dia-{view.business_date.isoformat()}.csv"'},
    )
