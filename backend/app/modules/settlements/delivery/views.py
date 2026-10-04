"""Responses of the delivery settlements API, built from the rows and the backend totals."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.delivery import additions, denominations, expenses, service, summary, transfers
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import ExpenseCategory, Settlement
from app.modules.settlements.ledger import service as ledger
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

    users = _user_names(
        db,
        [row.created_by]
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
        summary=_summary(totals, accounts),
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
        pending=totals.pending,
        expenses=totals.expenses,
        justified=totals.justified,
        difference=totals.difference,
        difference_kind=totals.difference_kind,
    )


def _user_names(db: Session, ids: list[int]) -> dict[int, str]:
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(set(ids))))
    return dict(rows.all())
