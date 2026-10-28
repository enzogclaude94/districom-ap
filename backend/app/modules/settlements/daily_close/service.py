"""«Cierre del día» de Cajas y Bancos (R9-2, Enzo's decisions 1 to 3): a read-only view of one business date.

- Every figure comes from operations whose `business_date` is the day, never their created_at, closed_at or received_at:
  a settlement closed on Tuesday with business date Monday belongs to Monday.
- Per cash box and bank: the opening balance is the ledger summed up to the day before; the day's operations are
  grouped by type (a settlement close by its settlement's type) into inflows and outflows; the closing balance is
  summed again up to the day, on its own, and checked against opening + net. A mismatch is an invariant error of the
  ledger, never a «diferencia de arqueo»: there is no physical count of a box, no bank statement and no
  reconciliation in Rendiciones.
- A reversal is its own category, named after what it reverses: never netted silently against the original.
- Without `settlements.employee_finance` an employee movement (and its reversal) reads «Movimiento de empleado
  (reservado)», grouped on its own; its amount still counts in every total.
- Nothing is signed, frozen or stored: no closing document, daily close, snapshot nor ledger operation. Store Sys
  never touches the ledger: it only shows in the settlements of the day.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.cheques import views as cheque_views
from app.modules.settlements.daily_close import schemas as s
from app.modules.settlements.delivery import closing
from app.modules.settlements.delivery.models import Settlement, SettlementClosing, SettlementDifference
from app.modules.settlements.employee_finance import privacy
from app.modules.settlements.expected import service as expected
from app.modules.settlements.ledger import service as ledger
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

ZERO = Decimal("0.00")
KINDS = ("CASH_BOX", "BANK")
COLLECTIONS_KINDS = ("DELIVERY_COLLECTIONS", "OUTLET_COLLECTIONS")

CATEGORY_LABELS = {
    "SETTLEMENT_CLOSE_DELIVERY": "Cierres de rendición de reparto",
    "SETTLEMENT_CLOSE_OUTLET": "Cierres de rendición de boca",
    "OUTLET_REMITTANCE": "Entregas a cuenta de boca",
    "PENDING_COLLECTION": "Cobro de pendientes",
    "CHEQUE_CREDIT": "Cheques acreditados",
    "CHEQUE_REJECTION": "Cheques rechazados",
    "PAYMENT": "Pagos a terceros",
    "DIRECT_EXPENSE": "Gastos directos",
    "EMPLOYEE_ADVANCE": "Adelantos a empleados",
    "EMPLOYEE_LOAN": "Préstamos a empleados",
    "EMPLOYEE_PAYOUT": "Pagos de liquidaciones",
    "CASH_TRANSFER": "Movimientos entre cajas",
    "BANK_DEPOSIT": "Depósitos en el banco",
    "BANK_WITHDRAWAL": "Extracciones del banco",
    "OWNER_CONTRIBUTION": "Aportes de socios",
    "OWNER_WITHDRAWAL": "Devoluciones de aportes",
    "OPENING_BALANCE": "Saldos iniciales",
    "ADJUSTMENT": "Ajustes",
    privacy.RESERVED_TYPE: privacy.RESERVED_LABEL,
}
REVERSAL_PREFIX = "REVERSAL:"


def category_label(key: str) -> str:
    if key.startswith(REVERSAL_PREFIX):
        reversed_key = key.removeprefix(REVERSAL_PREFIX)
        return f"Reverso de {CATEGORY_LABELS.get(reversed_key, reversed_key).lower()}"
    return CATEGORY_LABELS.get(key, key)


@dataclass
class _Category:
    operations: set[int] = field(default_factory=set)
    amount: Decimal = ZERO


@dataclass
class _AccountDay:
    inflows: dict[str, _Category] = field(default_factory=lambda: defaultdict(_Category))
    outflows: dict[str, _Category] = field(default_factory=lambda: defaultdict(_Category))
    moved: bool = False

    def add(self, key: str, operation_id: int, amount: Decimal) -> None:
        self.moved = True
        if amount == 0:
            return
        side = self.inflows if amount > 0 else self.outflows
        side[key].operations.add(operation_id)
        side[key].amount += abs(amount)


def daily_close(db: Session, on: date, *, can_read_employee_finance: bool) -> s.DailyCloseOut:
    flows = ledger.day_flows(db, on, KINDS)
    keys = _category_keys(db, flows, can_read_employee_finance)
    days: dict[int, _AccountDay] = defaultdict(_AccountDay)
    for flow in flows:
        days[flow.account_id].add(keys[flow.operation_id], flow.operation_id, flow.amount)

    opening = {b.account_id: b for b in ledger.account_balances(db, on=on - timedelta(days=1), include_inactive=True)}
    final = {b.account_id: b for b in ledger.account_balances(db, on=on, include_inactive=True)}
    # An account with nothing on the day shows only if it already existed then and is active today: one created
    # later did not exist yet, and one inactive today has no record of when it stopped being used.
    existing = ledger.accounts_existing_on(db, on, KINDS)
    accounts = [
        _account(balance, opening[account_id].balance, days.get(account_id, _AccountDay()))
        for account_id, balance in final.items()
        if balance.kind in KINDS
        and (
            (balance.active and account_id in existing)
            or balance.balance != 0
            or opening[account_id].balance != 0
            or account_id in days
        )
    ]
    accounts.sort(key=lambda a: (KINDS.index(a.kind), a.name.lower(), a.account_id))
    return s.DailyCloseOut(
        business_date=on,
        calculated_at=datetime.now(UTC),
        accounts=accounts,
        totals=[_totals(kind, [a for a in accounts if a.kind == kind]) for kind in KINDS],
        reconciled=all(a.reconciled for a in accounts),
        employee_movements_reserved=not can_read_employee_finance,
        settlements=_settlements(db, on),
        cheques=_cheques(db, on),
    )


def _category_keys(db: Session, flows: list[ledger.DayFlow], can_read: bool) -> dict[int, str]:
    """The category of each operation: its type, a settlement close by its settlement's type, a reversal after the
    operation it reverses; an employee movement or its reversal is reserved without the area."""
    by_operation = {flow.operation_id: flow for flow in flows}
    private: set[int] = set()
    if not can_read:
        private = privacy.private_operation_ids(
            db, set(by_operation), {op_id: flow.operation_type for op_id, flow in by_operation.items()}
        )
    closes = {
        flow.source_id
        for flow in by_operation.values()
        if flow.source_type == closing.SOURCE_TYPE and flow.source_id is not None
    }
    types = dict(db.execute(select(Settlement.id, Settlement.type).where(Settlement.id.in_(closes))).tuples().all())

    def base(type_: str, source_id: int | None) -> str:
        if type_ == "SETTLEMENT_CLOSE" and source_id in types:
            return f"SETTLEMENT_CLOSE_{types[source_id]}"
        return type_

    keys: dict[int, str] = {}
    for op_id, flow in by_operation.items():
        if op_id in private:
            keys[op_id] = privacy.RESERVED_TYPE
        elif flow.operation_type == "REVERSAL" and flow.reversed_type is not None:
            keys[op_id] = REVERSAL_PREFIX + base(flow.reversed_type, flow.source_id)
        else:
            keys[op_id] = base(flow.operation_type, flow.source_id)
    return keys


def _categories(side: dict[str, _Category]) -> list[s.CategoryOut]:
    rows = [
        s.CategoryOut(
            key=key,
            label=category_label(key),
            reverses=key.removeprefix(REVERSAL_PREFIX) if key.startswith(REVERSAL_PREFIX) else None,
            operations=len(category.operations),
            amount=category.amount,
        )
        for key, category in side.items()
    ]
    # Reversals right after what they reverse; the rest by amount.
    return sorted(rows, key=lambda c: (c.reverses or c.key, c.reverses is not None, -c.amount, c.key))


def _account(balance: ledger.AccountBalance, opening: Decimal, day: _AccountDay) -> s.AccountDayOut:
    inflows = sum((c.amount for c in day.inflows.values()), ZERO)
    outflows = sum((c.amount for c in day.outflows.values()), ZERO)
    net = inflows - outflows
    return s.AccountDayOut(
        account_id=balance.account_id,
        name=balance.name,
        kind="BANK" if balance.kind == "BANK" else "CASH_BOX",
        active=balance.active,
        opening_balance=opening,
        inflows=inflows,
        outflows=outflows,
        net=net,
        closing_balance=balance.balance,
        reconciled=opening + net == balance.balance,
        inflow_categories=_categories(day.inflows),
        outflow_categories=_categories(day.outflows),
    )


def _totals(kind: str, accounts: list[s.AccountDayOut]) -> s.KindTotalsOut:
    def total(values: Iterable[Decimal]) -> Decimal:
        return sum(values, ZERO)

    return s.KindTotalsOut(
        kind="BANK" if kind == "BANK" else "CASH_BOX",
        opening_balance=total(a.opening_balance for a in accounts),
        inflows=total(a.inflows for a in accounts),
        outflows=total(a.outflows for a in accounts),
        net=total(a.net for a in accounts),
        closing_balance=total(a.closing_balance for a in accounts),
    )


# --- settlements of the day ------------------------------------------------------------------------


def _settlements(db: Session, on: date) -> s.SettlementsDayOut:
    rows = list(
        db.scalars(
            select(Settlement)
            .where(Settlement.business_date == on)
            .order_by(Settlement.type, Settlement.sequence_number, Settlement.id)
        )
    )
    ids = [row.id for row in rows]
    employees = dict(
        db.execute(
            select(Employee.id, Employee.full_name).where(
                Employee.id.in_({r.employee_id for r in rows if r.employee_id})
            )
        )
        .tuples()
        .all()
    )
    outlets = dict(
        db.execute(select(Outlet.id, Outlet.name).where(Outlet.id.in_({r.outlet_id for r in rows if r.outlet_id})))
        .tuples()
        .all()
    )
    closings = {
        c.id: c
        for c in db.scalars(
            select(SettlementClosing).where(
                SettlementClosing.id.in_({r.current_closing_id for r in rows if r.current_closing_id})
            )
        )
    }
    close_ops = [r.current_close_operation_id for r in rows if r.current_close_operation_id]
    differences = {
        d.close_operation_id: d
        for d in db.scalars(select(SettlementDifference).where(SettlementDifference.close_operation_id.in_(close_ops)))
    }
    collected = ledger.operation_kind_totals(db, close_ops, COLLECTIONS_KINDS)
    pending = expected.pending_settlements(db, ids)
    items = [_settlement(row, employees, outlets, closings, differences, collected, row.id in pending) for row in rows]
    return s.SettlementsDayOut(
        total=len(items),
        open=sum(i.financial_state == "NOT_CLOSED" for i in items),
        closed=sum(i.financial_state == "CLOSED" for i in items),
        being_corrected=sum(i.being_corrected for i in items),
        without_expected=sum(i.expected_missing for i in items),
        store_sys_pending=sum(i.expected_update_pending for i in items),
        items=items,
    )


def _settlement(
    row: Settlement,
    employees: dict[int, str],
    outlets: dict[int, str],
    closings: dict[int, SettlementClosing],
    differences: dict[int, SettlementDifference],
    collected: dict[int, Decimal],
    update_pending: bool,
) -> s.SettlementDayOut:
    name = outlets.get(row.outlet_id or 0, "") if row.type == "OUTLET" else employees.get(row.employee_id or 0, "")
    state = closing.financial_state(row)
    effective = closings.get(row.current_closing_id) if row.current_closing_id else None
    expected_amount: Decimal | None
    justified: Decimal | None = None
    difference: Decimal | None = None
    if effective is None:
        # Not closed: the amount loaded, never a draft justified nor difference.
        expected_amount = row.expected_amount
        origin = _live_origin(row)
    else:
        expected_amount, origin = _closed_expected(row, effective, collected)
        found = differences.get(row.current_close_operation_id or 0)
        difference = ZERO if found is None else (found.amount if found.kind == "SURPLUS" else -found.amount)
        justified = expected_amount + difference
    return s.SettlementDayOut(
        id=row.id,
        type="OUTLET" if row.type == "OUTLET" else "DELIVERY",
        name=name,
        sequence_number=row.sequence_number,
        financial_state=state,
        being_corrected=expected.being_corrected(row),
        expected_missing=row.expected_amount is None and effective is None,
        expected_update_pending=update_pending,
        close_kind=None
        if effective is None
        else ("ADMINISTRATIVE_ZERO" if effective.kind == "ADMINISTRATIVE_ZERO" else "FINANCIAL"),
        expected_amount=expected_amount,
        expected_origin=origin,
        justified=justified,
        difference=difference,
    )


def _live_origin(row: Settlement) -> s.ExpectedOriginOut | None:
    if row.expected_amount is None:
        return None
    return s.ExpectedOriginOut(
        source="STORE_SYS" if row.expected_source == "STORE_SYS" else "MANUAL", legacy=row.expected_event_id is None
    )


def _closed_expected(
    row: Settlement, effective: SettlementClosing, collected: dict[int, Decimal]
) -> tuple[Decimal, s.ExpectedOriginOut]:
    """The expected amount the close in effect was made against (R9-1 freezes it on the close). A close of before
    R9-1 has none frozen: what it took out of its collections account in the ledger is that amount."""
    if effective.expected_amount is not None:
        return effective.expected_amount, s.ExpectedOriginOut(
            source="STORE_SYS" if effective.expected_source == "STORE_SYS" else "MANUAL",
            legacy=effective.expected_event_id is None,
            external_reference=effective.expected_reference,
            revision=effective.expected_revision,
        )
    taken = -collected.get(row.current_close_operation_id or 0, ZERO) if effective.operation_id else ZERO
    return taken, s.ExpectedOriginOut(source="MANUAL", legacy=True)


# --- cheques --------------------------------------------------------------------------------------


def _cheques(db: Session, on: date) -> s.ChequesDayOut:
    portfolio = ledger.cheques_held(db, "CHEQUE_PORTFOLIO", on=on)
    in_transit = ledger.cheques_held(db, "CHEQUES_IN_TRANSIT", on=on)
    day = cheque_views.day_documents(db, on)
    return s.ChequesDayOut(
        portfolio_count=len(portfolio),
        portfolio_amount=sum(portfolio.values(), ZERO),
        in_transit_count=len(in_transit),
        in_transit_amount=sum(in_transit.values(), ZERO),
        deposited_count=day.deposited.count,
        deposited_amount=day.deposited.amount,
        credited_count=day.credited.count,
        credited_amount=day.credited.amount,
        rejected_count=day.rejected.count,
        rejected_amount=day.rejected.amount,
        returned_count=day.returned.count,
        returned_amount=day.returned.amount,
    )
