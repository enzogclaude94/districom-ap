"""Responses of the boca de expendio settlements API, built with the same board as a delivery settlement."""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.modules.settlements.delivery import closing, service, summary
from app.modules.settlements.delivery import schemas as ds
from app.modules.settlements.delivery import views as delivery_views
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.outlet_settlements import schemas as s
from app.modules.settlements.outlet_settlements.service import responsible_suggestions
from app.platform.outlets import service as outlets
from app.platform.outlets.models import Outlet


def settlement(db: Session, settlement_id: int) -> s.OutletSettlementOut:
    row = service.get(db, settlement_id)
    if row.outlet_id is None:  # never for OUTLET (identity_by_type)
        raise service.not_found()
    outlet = outlets.get(db, row.outlet_id)
    board = delivery_views.board(db, row)
    board["summary"] = _summary(board["summary"], row)
    return s.OutletSettlementOut(
        **board,
        outlet_id=outlet.id,
        outlet_code=outlet.code,
        outlet_name=outlet.name,
        outlet_active=outlet.active,
        expected_amount=row.expected_amount,
        responsible_suggestions=_suggestions(db, row),
    )


def closing_preview(db: Session, settlement_id: int) -> s.OutletClosingPreviewOut:
    preview = dict(delivery_views.closing_preview(db, settlement_id))
    row = service.get(db, settlement_id)
    preview["summary"] = _summary(preview["summary"], row)
    return s.OutletClosingPreviewOut(**preview, responsible_suggestions=_suggestions(db, row))


def items(db: Session, rows: Iterable[tuple[Settlement, Outlet]]) -> list[s.OutletSettlementItem]:
    rows = list(rows)
    totals = summary.totals(db, [row.id for row, _ in rows])
    return [
        s.OutletSettlementItem(
            id=row.id,
            business_date=row.business_date,
            outlet_id=outlet.id,
            outlet_code=outlet.code,
            outlet_name=outlet.name,
            sequence_number=row.sequence_number,
            expected_amount=row.expected_amount,
            external_reference=row.external_reference,
            status=row.status,
            version=row.version,
            financial_state=closing.financial_state(row),
            administrative_zero=closing.administrative_close_in_effect(row),
            created_at=row.created_at,
            justified=totals[row.id].justified,
            difference=None if totals[row.id].expected_missing else totals[row.id].difference,
        )
        for row, outlet in rows
    ]


def _summary(board: ds.Summary, row: Settlement) -> s.OutletSummary:
    return s.OutletSummary(**dict(board), expected_missing=row.expected_amount is None)


def _suggestions(db: Session, row: Settlement) -> list[s.ResponsibleSuggestion]:
    return [s.ResponsibleSuggestion(employee_id=e.id, full_name=e.full_name) for e in responsible_suggestions(db, row)]
