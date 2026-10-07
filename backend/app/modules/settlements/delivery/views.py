"""Responses of the delivery settlements API, built from the rows and the backend totals."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.cheques import service as cheque_service
from app.modules.settlements.cheques import views as cheque_views
from app.modules.settlements.delivery import (
    additions,
    closing,
    denominations,
    expenses,
    service,
    summary,
    transfers,
)
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import (
    DifferenceAllocation,
    ExpenseCategory,
    Settlement,
    SettlementClosing,
    SettlementDifference,
)
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employees_of_accounts
from app.modules.settlements.pending import service as pending_service
from app.modules.settlements.pending import views as pending_views
from app.platform.auth.models import User
from app.platform.parties.models import Employee


def settlement(db: Session, settlement_id: int) -> s.SettlementOut:
    row = service.get(db, settlement_id)
    lines = service.count_lines(db, row.id)
    added = additions.of(db, row.id)
    sent = transfers.of(db, row.id)
    events = transfers.events_of(db, [t.id for t in sent])
    spent = expenses.of(db, row.id)
    totals = summary.totals(db, [row.id])[row.id]
    difference = closing.current_difference(db, row)
    allocated = closing.allocations_of(db, [difference.id] if difference else [])
    in_effect = closing.close_of(db, row.current_close_operation_id) if row.current_close_operation_id else None

    users = _user_names(
        db,
        [row.created_by]
        + [u for u in (row.closed_by, row.reopened_by) if u]
        + _difference_users([difference] if difference else [], allocated)
        + [a.created_by for a in added]
        + [a.voided_by for a in added if a.voided_by]
        + [t.status_changed_by for t in sent]
        + [e.changed_by for es in events.values() for e in es]
        + [e.created_by for e in spent]
        + [e.voided_by for e in spent if e.voided_by],
    )
    accounts = {a.account_id: a.name for a in ledger.account_balances(db, include_inactive=True)}
    categories = {c.id: c.name for c in db.scalars(select(ExpenseCategory))}
    employee = db.get(Employee, row.employee_id)

    return s.SettlementOut(
        id=row.id,
        type=row.type,
        business_date=row.business_date,
        employee_id=row.employee_id,
        employee_name=employee.full_name if employee else "",
        sequence_number=row.sequence_number,
        expected_amount=row.expected_amount,
        expected_source=row.expected_source,
        external_reference=row.external_reference,
        notes=row.notes,
        status=row.status,
        version=row.version,
        created_by=row.created_by,
        created_by_name=users[row.created_by],
        created_at=row.created_at,
        updated_at=row.updated_at,
        same_day_settlement_ids=service.same_day(db, row),
        count_lines=[
            s.CountLineOut(
                denomination_code=line.denomination_code,
                denomination_value=line.denomination_value,
                quantity=line.quantity,
                amount=line.denomination_value * line.quantity,
                destination_account_id=line.destination_account_id,
                destination_account_name=accounts[line.destination_account_id],
            )
            for line in lines
        ],
        additions=[
            s.AdditionOut(
                id=a.id,
                amount=a.amount,
                destination_account_id=a.destination_account_id,
                destination_account_name=accounts[a.destination_account_id],
                description=a.description,
                created_by=a.created_by,
                created_by_name=users[a.created_by],
                created_at=a.created_at,
                voided=a.voided_at is not None,
                voided_by=a.voided_by,
                voided_by_name=users[a.voided_by] if a.voided_by else None,
                voided_at=a.voided_at,
                void_reason=a.void_reason,
            )
            for a in added
        ],
        transfers=[
            s.TransferOut(
                id=t.id,
                amount=t.amount,
                bank_account_id=t.bank_account_id,
                bank_account_name=accounts[t.bank_account_id],
                reference=t.reference,
                payer_name=t.payer_name,
                status=t.status,
                status_reason=t.status_reason,
                status_changed_by=t.status_changed_by,
                status_changed_by_name=users[t.status_changed_by],
                status_changed_at=t.status_changed_at,
                created_by=t.created_by,
                created_at=t.created_at,
                events=[
                    s.TransferEventOut(
                        from_status=e.from_status,
                        to_status=e.to_status,
                        reason=e.reason,
                        is_correction=e.is_correction,
                        changed_by=e.changed_by,
                        changed_by_name=users[e.changed_by],
                        changed_at=e.changed_at,
                    )
                    for e in events[t.id]
                ],
            )
            for t in sent
        ],
        expenses=[
            s.ExpenseOut(
                id=e.id,
                category_id=e.category_id,
                category_name=categories[e.category_id],
                amount=e.amount,
                description=e.description,
                created_by=e.created_by,
                created_by_name=users[e.created_by],
                created_at=e.created_at,
                voided=e.voided_at is not None,
                voided_by=e.voided_by,
                voided_by_name=users[e.voided_by] if e.voided_by else None,
                voided_at=e.voided_at,
                void_reason=e.void_reason,
            )
            for e in spent
        ],
        pending_collections=pending_views.items(db, pending_service.of(db, row.id)),
        cheques=cheque_views.items(db, cheque_service.of(db, row.id)),
        summary=_summary(totals, accounts),
        financial_state=closing.financial_state(row),
        financial_state_detail=closing.financial_state_detail(row),
        close_in_effect=_close_ref(in_effect),
        closed_by=row.closed_by,
        closed_by_name=users[row.closed_by] if row.closed_by else None,
        closed_at=row.closed_at,
        reopened_by=row.reopened_by,
        reopened_by_name=users[row.reopened_by] if row.reopened_by else None,
        reopened_at=row.reopened_at,
        reopen_reason=row.reopen_reason,
        current_difference=_difference(db, difference, allocated, users) if difference else None,
    )


def closing_preview(db: Session, settlement_id: int) -> s.ClosingPreviewOut:
    preview = closing.preview(db, settlement_id)
    plan = preview.plan
    row = plan.settlement
    accounts = {a.account_id: a for a in ledger.account_balances(db, include_inactive=True)}
    in_effect = closing.close_of(db, row.current_close_operation_id) if row.current_close_operation_id else None
    portfolio = ledger.system_account_id(db, "cheque_portfolio")
    return s.ClosingPreviewOut(
        settlement_id=row.id,
        version=row.version,
        status=row.status,
        financial_state=closing.financial_state(row),
        summary=_summary(plan.totals, {a.account_id: a.name for a in accounts.values()}),
        difference=plan.difference,
        difference_kind=plan.totals.difference_kind,
        amount_to_allocate=abs(plan.difference),
        blockers=preview.blockers,
        preview_token=preview.token,
        legs=[
            s.LegOut(
                account_id=portfolio if kind == "cheque" else ref,
                account_name=accounts[portfolio if kind == "cheque" else ref].name,
                account_kind=accounts[portfolio if kind == "cheque" else ref].kind,
                cheque_id=ref if kind == "cheque" else None,
                amount=amount,
            )
            for (kind, ref), amount in sorted(plan.legs.items())
        ],
        pending_collections=pending_views.items(db, pending_service.of(db, row.id)),
        cheques=cheque_views.items(db, cheque_service.of(db, row.id)),
        close_in_effect=_close_ref(in_effect),
    )


def history(db: Session, settlement_id: int) -> s.HistoryOut:
    row = service.get(db, settlement_id)
    closings = closing.closings(db, row.id)
    diffs = closing.differences(db, row.id)
    allocated = closing.allocations_of(db, [d.id for d in diffs])
    reopened = closing.reopenings(db, row.id)
    details = {c.operation_id: ledger.operation_detail(db, c.operation_id) for c in closings}
    users = _user_names(
        db,
        [c.closed_by for c in closings] + [r.reopened_by for r in reopened] + _difference_users(diffs, allocated),
    )
    employees = employees_of_accounts(
        db, sorted({entry.account_id for detail in details.values() for entry in detail.entries})
    )
    return s.HistoryOut(
        settlement_id=row.id,
        version=row.version,
        status=row.status,
        financial_state=closing.financial_state(row),
        closings=[
            s.ClosingOut(
                settlement_version=c.settlement_version,
                operation_id=c.operation_id,
                reversal_operation_id=c.reversal_operation_id,
                reversed_by_operation_id=details[c.operation_id].reversed_by_operation_id,
                in_effect=c.operation_id == row.current_close_operation_id,
                closed_by=c.closed_by,
                closed_by_name=users[c.closed_by],
                closed_at=c.closed_at,
                business_date=details[c.operation_id].business_date,
                legs=[
                    s.LegOut(
                        account_id=entry.account_id,
                        account_name=entry.account_name,
                        account_kind=entry.account_kind,
                        employee_id=employees.get(entry.account_id),
                        cheque_id=entry.cheque_id,
                        amount=entry.amount,
                    )
                    for entry in details[c.operation_id].entries
                ],
            )
            for c in closings
        ],
        reopenings=[
            s.ReopeningOut(
                from_version=r.from_version,
                to_version=r.to_version,
                reason=r.reason,
                reopened_by=r.reopened_by,
                reopened_by_name=users[r.reopened_by],
                reopened_at=r.reopened_at,
                close_operation_id=r.close_operation_id,
            )
            for r in reopened
        ],
        differences=[_difference(db, d, allocated, users) for d in diffs],
    )


def items(db: Session, rows: Iterable[tuple[Settlement, str]]) -> list[s.SettlementItem]:
    rows = list(rows)
    totals = summary.totals(db, [row.id for row, _ in rows])
    return [
        s.SettlementItem(
            id=row.id,
            type=row.type,
            business_date=row.business_date,
            employee_id=row.employee_id,
            employee_name=name,
            sequence_number=row.sequence_number,
            expected_amount=row.expected_amount,
            external_reference=row.external_reference,
            status=row.status,
            version=row.version,
            financial_state=closing.financial_state(row),
            created_at=row.created_at,
            justified=totals[row.id].justified,
            difference=totals[row.id].difference,
        )
        for row, name in rows
    ]


def denomination_list(db: Session, include_inactive: bool) -> list[s.DenominationOut]:
    boxes = denominations.cash_boxes(db)
    result = []
    for d in denominations.configured(db):
        if not d.active and not include_inactive:
            continue
        box = boxes.get(d.suggested_destination) if d.suggested_destination else None
        result.append(
            s.DenominationOut(
                code=d.code,
                value=d.value,
                active=d.active,
                suggested_destination=d.suggested_destination,
                suggested_destination_account_id=box.account_id if box else None,
                suggested_destination_name=box.name if box else None,
            )
        )
    return result


def category(row: ExpenseCategory) -> s.ExpenseCategoryOut:
    return s.ExpenseCategoryOut(id=row.id, name=row.name, active=row.active)


def _summary(totals: summary.Totals, accounts: dict[int, str]) -> s.Summary:
    return s.Summary(
        expected=totals.expected,
        cash_counted=totals.cash_counted,
        cash_additions=totals.cash_additions,
        cash_total=totals.cash_total,
        cash_by_box=[
            s.CashByBox(
                account_id=account_id,
                account_name=accounts[account_id],
                counted=box.counted,
                additions=box.additions,
                total=box.total,
            )
            for account_id, box in sorted(totals.by_box.items())
        ],
        transfers_verified=totals.transfers["VERIFIED"],
        transfers_declared=totals.transfers["DECLARED"],
        transfers_rejected=totals.transfers["REJECTED"],
        declared_transfers_count=totals.declared_transfers_count,
        cheques=totals.cheques,
        unverified_echeqs_count=totals.unverified_echeqs_count,
        pending=totals.pending,
        expenses=totals.expenses,
        justified=totals.justified,
        difference=totals.difference,
        difference_kind=totals.difference_kind,
    )


def _close_ref(row: SettlementClosing | None) -> s.CloseRef | None:
    if row is None:
        return None
    return s.CloseRef(operation_id=row.operation_id, settlement_version=row.settlement_version, closed_at=row.closed_at)


def _difference_users(diffs: list[SettlementDifference], allocated: dict[int, list[DifferenceAllocation]]) -> list[int]:
    ids = [d.created_by for d in diffs] + [d.superseded_by for d in diffs if d.superseded_by]
    return ids + [a.decided_by for rows in allocated.values() for a in rows]


def _difference(
    db: Session,
    row: SettlementDifference,
    allocated: dict[int, list[DifferenceAllocation]],
    users: dict[int, str],
) -> s.DifferenceOut:
    employee_ids = {a.employee_id for a in allocated[row.id] if a.employee_id}
    names = dict(db.execute(select(Employee.id, Employee.full_name).where(Employee.id.in_(employee_ids))).all())
    return s.DifferenceOut(
        id=row.id,
        settlement_version=row.settlement_version,
        kind=row.kind,
        amount=row.amount,
        close_operation_id=row.close_operation_id,
        created_by=row.created_by,
        created_by_name=users[row.created_by],
        created_at=row.created_at,
        superseded_at=row.superseded_at,
        superseded_by=row.superseded_by,
        superseded_by_name=users[row.superseded_by] if row.superseded_by else None,
        allocations=[
            s.AllocationOut(
                target=a.target,
                employee_id=a.employee_id,
                employee_name=names.get(a.employee_id) if a.employee_id else None,
                amount=a.amount,
                reason=a.reason,
                decided_by=a.decided_by,
                decided_by_name=users[a.decided_by],
                decided_at=a.decided_at,
            )
            for a in allocated[row.id]
        ],
    )


def _user_names(db: Session, ids: list[int]) -> dict[int, str]:
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(set(ids))))
    return dict(rows.all())
