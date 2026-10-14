"""Responses of the boca de expendio settlements API, built with the same board as a delivery settlement."""

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.delivery import closing, service, summary
from app.modules.settlements.delivery import schemas as ds
from app.modules.settlements.delivery import views as delivery_views
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import advances_account_of
from app.modules.settlements.outlet_settlements import remittances
from app.modules.settlements.outlet_settlements import schemas as s
from app.modules.settlements.outlet_settlements.models import OutletRemittance
from app.modules.settlements.outlet_settlements.service import responsible_suggestions
from app.platform.auth.models import User
from app.platform.outlets import service as outlets
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

ZERO = Decimal("0.00")


def settlement(db: Session, settlement_id: int) -> s.OutletSettlementOut:
    row = service.get(db, settlement_id)
    if row.outlet_id is None:  # never for OUTLET (identity_by_type)
        raise service.not_found()
    outlet = outlets.get(db, row.outlet_id)
    board = delivery_views.board(db, row)
    board["summary"] = _summary(board["summary"], row)
    return s.OutletSettlementOut(
        **board,
        remittances=remittance_items(db, row),
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
    preview["summary"] = _summary(preview["summary"], row, closing=True)
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
            # No difference while it is OPEN (R7-3): only a close gives one.
            difference=None if totals[row.id].expected_missing or row.status == "OPEN" else totals[row.id].difference,
        )
        for row, outlet in rows
    ]


def remittance_preview(plan: remittances.Plan, advances_account_id: int | None) -> s.RemittancePreviewOut:
    return s.RemittancePreviewOut(
        legs=[
            s.RemittanceLegOut(account_id=plan.destination.id, account_name=plan.destination.name, amount=plan.amount),
            s.RemittanceLegOut(account_id=advances_account_id, account_name=plan.advances_name, amount=-plan.amount),
        ],
        remitted_before=plan.remitted_before,
        remitted_after=plan.remitted_after,
        expected=plan.settlement.expected_amount,
        excess=plan.excess,
        warning=remittances.excess_error(plan).message if plan.excess is not None else None,
    )


def remittance_items(db: Session, row: Settlement) -> list[s.RemittanceOut]:
    rows = remittances.of(db, row.id)
    if not rows:
        return []
    consolidated = remittances.consolidated_ids(db, row.current_close_operation_id)
    accounts = {a.account_id: a.name for a in ledger.account_balances(db, include_inactive=True)}
    users = dict(
        db.execute(
            select(User.id, User.full_name).where(
                User.id.in_({r.created_by for r in rows} | {r.reversed_by for r in rows if r.reversed_by})
            )
        ).all()
    )
    employees = dict(
        db.execute(
            select(Employee.id, Employee.full_name).where(
                Employee.id.in_({r.delivered_by_employee_id for r in rows if r.delivered_by_employee_id})
            )
        ).all()
    )
    return [_remittance_out(r, accounts, users, employees, consolidated) for r in rows]


def _remittance_out(
    r: OutletRemittance,
    accounts: dict[int, str],
    users: dict[int, str],
    employees: dict[int, str],
    consolidated: set[int],
) -> s.RemittanceOut:
    return s.RemittanceOut(
        id=r.id,
        amount=r.amount,
        delivered_at=r.delivered_at,
        destination_account_id=r.destination_account_id,
        destination_account_name=accounts[r.destination_account_id],
        delivered_by_employee_id=r.delivered_by_employee_id,
        delivered_by_name=employees.get(r.delivered_by_employee_id) if r.delivered_by_employee_id else None,
        reference=r.reference,
        operation_id=r.operation_id,
        status="REVERSED" if r.reversal_operation_id is not None else "IN_EFFECT",
        consolidated=r.id in consolidated,
        created_by=r.created_by,
        created_by_name=users.get(r.created_by, ""),
        created_at=r.created_at,
        reversal_operation_id=r.reversal_operation_id,
        reversed_by=r.reversed_by,
        reversed_by_name=users.get(r.reversed_by) if r.reversed_by else None,
        reversed_at=r.reversed_at,
        reversal_reason=r.reversal_reason,
    )


def advances_account(db: Session, outlet_id: int) -> int | None:
    return advances_account_of(db, outlet_id)


def _summary(board: ds.Summary, row: Settlement, *, closing: bool = False) -> s.OutletSummary:
    """The boca's board. While OPEN it says how far the justified total is from the expected amount, never a
    difference (Enzo's R7-3 point 15); the close preview and a CLOSED settlement show the difference."""
    values = dict(board)
    missing = row.expected_amount is None
    remaining = above = None
    if not missing:
        gap = board.expected - board.justified
        remaining, above = (gap, None) if gap >= 0 else (ZERO, -gap)
    if row.status == "OPEN" and not closing:
        values["difference"] = None
        values["difference_kind"] = None
    return s.OutletSummary(**values, expected_missing=missing, remaining=remaining, justified_above_expected=above)


def _suggestions(db: Session, row: Settlement) -> list[s.ResponsibleSuggestion]:
    return [s.ResponsibleSuggestion(employee_id=e.id, full_name=e.full_name) for e in responsible_suggestions(db, row)]
