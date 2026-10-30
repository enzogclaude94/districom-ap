"""Payouts of closed internal payrolls (R8-5, ADR 010, Enzo's R8-5 decisions).

A payout hands an employee money for the net of a CLOSED payroll: the net of the payroll's close in effect, the one
the client saw (`payroll_closing_id`; a different one is `payroll_version_changed`). It is the document of its own
operation (`EMPLOYEE_PAYOUT`, source `employee_payout:<id>`), in one or more items:

    CASH       an active cash box   -item
    TRANSFER   an active bank       -item
               the internal account +total

| Action | Who | Ledger |
|---|---|---|
| pay | operator or admin of the area + operator in Rendiciones (it moves a cash box or the bank) | EMPLOYEE_PAYOUT |
| reverse | admin of the area + operator in Rendiciones, with a reason | REVERSAL, on the payout's date |

- Several payouts per close: paid = Σ payouts in effect of the close in effect; pending = net - paid; always
  0 <= paid <= net (the database checks it at commit). The payment state is derived, never stored: «Sin pagar»,
  «Pagada parcialmente», «Pagada». The internal account's balance is never the limit (Enzo's Q3): paying 650.000 to
  an account at -400.000 leaves it at +250.000. A NO_ACCRUAL close with a net > 0 is paid like any other (Q1).
- Date: from the period's end up to today (business timezone; Q2). Money handed over before the period ends is an
  advance (R8-2). The payroll's `payment_date` is informative only: never checked.
- Funds: the rules of an advance, a payment or a direct expense. A cash box never goes negative on the date or any
  later day; a bank never goes into an overdraft nor deeper into one. A mixed payout takes every account, in id
  order, and publishes everything or nothing.
- The employee may be inactive: a closed payroll is still owed.
- Reversal: the whole payout, with a reason; the REVERSAL of its operation on its date puts the money back (no funds
  check). As every reversal of the ledger, it needs its accounts still active (`account_inactive`), like R8-2/R8-3.
  A payroll with payouts in effect does not reopen: reverse them first (`payroll_has_payouts`).

Lock order (every R8 flow): the internal account, the payroll (FOR UPDATE: serializes the payouts of a payroll with
each other, with its reopening and its close), then the cash boxes and banks in id order (before the insert takes them
FOR SHARE), then the ledger's locks. Idempotent by `client_request_id`; the ledger request ids derive from the
client's.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.service import money
from app.modules.settlements.employee_finance import payrolls
from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.employee_finance.models import (
    EmployeePayout,
    EmployeePayoutItem,
    EmployeePayroll,
    EmployeePayrollClosing,
)
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import lock_account
from app.platform.audit.service import record_change
from app.platform.auth.access import has_area_access, has_module_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "employee_payout"
ENTITY = "employee_payout"
EVENT = "settlements.employee_finance.payout"
OPERATION_TYPE = "EMPLOYEE_PAYOUT"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("5f0c2a8e-7b3d-4e19-a6c4-91d2e8b07f35")
METHOD_KINDS = {"CASH": "CASH_BOX", "TRANSFER": "BANK"}
MAX_ITEMS = 10
CENT = Decimal("0.01")
ZERO = Decimal("0")

Method = Literal["CASH", "TRANSFER"]
PaymentStatus = Literal["UNPAID", "PARTIALLY_PAID", "PAID"]


@dataclass(frozen=True)
class Item:
    method: str
    account_id: int
    amount: Decimal


@dataclass(frozen=True)
class Command:
    """What the client asked, normalized."""

    payroll_id: int
    payroll_closing_id: int
    business_date: date
    items: tuple[Item, ...]
    reference: str | None

    @property
    def amount(self) -> Decimal:
        return sum((i.amount for i in self.items), ZERO)


@dataclass(frozen=True)
class Leg:
    account_id: int
    account_name: str
    account_kind: str
    amount: Decimal


@dataclass(frozen=True)
class Payment:
    """The payment state of a payroll's close in effect, always derived."""

    net: Decimal
    paid: Decimal
    pending: Decimal
    status: PaymentStatus


@dataclass(frozen=True)
class Plan:
    """A payout validated against the payroll, the roles and the funds: what it would publish."""

    command: Command
    payroll: EmployeePayroll
    employee: Employee
    payment: Payment
    legs: list[Leg]

    @property
    def pending_after(self) -> Decimal:
        return self.payment.pending - self.command.amount


@dataclass(frozen=True)
class Created:
    id: int  # the payout
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


def command(
    payroll_id: int,
    payroll_closing_id: int,
    business_date: date,
    items: list[Item],
    reference: str | None,
) -> Command:
    """Normalize a payout: amounts to the cent, one item per account, CASH or TRANSFER only."""
    if not items:
        raise AppError(422, "items_required", "Indicá al menos un medio de pago.")
    if len(items) > MAX_ITEMS:
        raise AppError(422, "too_many_items", f"Un pago tiene como máximo {MAX_ITEMS} medios.")
    normalized: list[Item] = []
    for item in items:
        if item.method not in METHOD_KINDS:
            raise AppError(
                422,
                "invalid_payout_method",
                "Un pago de liquidación se hace en efectivo desde una caja o por transferencia desde un banco.",
                {"method": item.method},
            )
        amount = item.amount.quantize(CENT)
        if amount <= 0:
            raise AppError(422, "invalid_item_amount", "El importe de cada medio tiene que ser mayor a cero.")
        normalized.append(Item(item.method, item.account_id, amount))
    if len({i.account_id for i in normalized}) != len(normalized):
        raise AppError(
            422, "duplicate_payout_account", "Cada caja o banco va una sola vez en el pago: sumá los importes."
        )
    return Command(
        payroll_id=payroll_id,
        payroll_closing_id=payroll_closing_id,
        business_date=business_date,
        items=tuple(normalized),
        reference=" ".join((reference or "").split()) or None,
    )


# --- the payment state ----------------------------------------------------------------------------


def paid(db: Session, closing_id: int) -> Decimal:
    """Σ payouts in effect of a close."""
    total = db.scalar(
        select(func.coalesce(func.sum(EmployeePayout.amount), 0)).where(
            EmployeePayout.payroll_closing_id == closing_id, EmployeePayout.reversal_operation_id.is_(None)
        )
    )
    return Decimal(total or 0)


def paid_by_closing(db: Session, closing_ids: Collection[int]) -> dict[int, Decimal]:
    """Σ payouts in effect of each close, in one query (R9-4: lists asked once per row); 0 when none."""
    if not closing_ids:
        return {}
    rows = db.execute(
        select(EmployeePayout.payroll_closing_id, func.sum(EmployeePayout.amount))
        .where(EmployeePayout.payroll_closing_id.in_(closing_ids), EmployeePayout.reversal_operation_id.is_(None))
        .group_by(EmployeePayout.payroll_closing_id)
    ).tuples()
    paid_amounts = dict.fromkeys(closing_ids, Decimal(0))
    paid_amounts.update({closing_id: Decimal(total) for closing_id, total in rows})
    return paid_amounts


def payment(db: Session, payroll: EmployeePayroll) -> Payment | None:
    """The payment state of a CLOSED payroll; None otherwise (an open or cancelled one is not payable)."""
    if payroll.status != "CLOSED" or payroll.current_closing_id is None:
        return None
    closing = db.get(EmployeePayrollClosing, payroll.current_closing_id)
    assert closing is not None
    return state(closing.net, paid(db, closing.id))


def state(net: Decimal, paid_amount: Decimal) -> Payment:
    pending = net - paid_amount
    status: PaymentStatus = "PAID" if pending <= 0 else "PARTIALLY_PAID" if paid_amount > 0 else "UNPAID"
    return Payment(net=net, paid=paid_amount, pending=pending, status=status)


def pending_by_employee(db: Session, employee_id: int) -> Decimal:
    """What the employee's CLOSED payrolls still owe them (never the internal account's balance)."""
    paid_q = (
        select(EmployeePayout.payroll_closing_id, func.sum(EmployeePayout.amount).label("paid"))
        .where(EmployeePayout.employee_id == employee_id, EmployeePayout.reversal_operation_id.is_(None))
        .group_by(EmployeePayout.payroll_closing_id)
        .subquery()
    )
    total = db.scalar(
        select(func.coalesce(func.sum(EmployeePayrollClosing.net - func.coalesce(paid_q.c.paid, 0)), 0))
        .select_from(EmployeePayroll)
        .join(EmployeePayrollClosing, EmployeePayrollClosing.id == EmployeePayroll.current_closing_id)
        .outerjoin(paid_q, paid_q.c.payroll_closing_id == EmployeePayrollClosing.id)
        .where(EmployeePayroll.employee_id == employee_id, EmployeePayroll.status == "CLOSED")
    )
    return Decimal(total or 0)


# --- paying ---------------------------------------------------------------------------------------


def plan(db: Session, actor: User, cmd: Command, *, payroll: EmployeePayroll | None = None) -> Plan:
    """Validate a payout and say what it would publish. Funds are checked here as a preview too; the ledger checks
    them again, under its locks, when publishing. `payroll` is the payroll already locked by `create`."""
    _require_pay_roles(actor)
    if payroll is None:
        payroll = payrolls.get(db, cmd.payroll_id)
    if payroll.status != "CLOSED" or payroll.current_closing_id is None:
        raise AppError(
            409,
            "payroll_not_closed",
            "Solo se paga una liquidación cerrada.",
            {"payroll_id": payroll.id, "status": payroll.status},
        )
    if payroll.current_closing_id != cmd.payroll_closing_id:
        raise AppError(
            409,
            "payroll_version_changed",
            "La liquidación cambió mientras la revisabas. Actualizá y revisá el pago nuevamente.",
            {"payroll_id": payroll.id, "payroll_closing_id": payroll.current_closing_id},
        )
    today = business_today()
    if cmd.business_date > today:
        raise AppError(422, "future_business_date", "La fecha del pago no puede ser futura.")
    if cmd.business_date < payroll.period_end:
        raise AppError(
            422,
            "payout_before_period_end",
            f"El pago no puede ser anterior al fin del período ({payroll.period_end:%d/%m/%Y}). La plata entregada "
            "antes es un adelanto.",
            {"period_end": payroll.period_end.isoformat()},
        )
    employee = db.get(Employee, payroll.employee_id)
    assert employee is not None
    current = payment(db, payroll)
    assert current is not None
    if current.pending <= 0:
        raise AppError(409, "payroll_fully_paid", "La liquidación ya está pagada.", {"payroll_id": payroll.id})
    if cmd.amount > current.pending:
        raise AppError(
            409,
            "payout_exceeds_pending",
            f"El pago (${money(cmd.amount)}) supera lo pendiente de la liquidación (${money(current.pending)}).",
            {"pending": money(current.pending), "amount": money(cmd.amount)},
        )
    legs: list[Leg] = []
    for item in sorted(cmd.items, key=lambda i: i.account_id):
        account = _payout_account(db, item)
        lowest, on = ledger.lowest_balance_since(db, account.account_id, cmd.business_date)
        if lowest < item.amount:
            raise AppError(
                409,
                "insufficient_funds",
                f"{account.name} quedaría con saldo negativo el {on:%d/%m/%Y}: no alcanza para pagar "
                f"${money(item.amount)}.",
                {
                    "account_id": account.account_id,
                    "business_date": on.isoformat(),
                    "balance": money(lowest),
                    "amount": money(-item.amount),
                },
            )
        legs.append(Leg(account.account_id, account.name, account.kind, -item.amount))
    legs.append(Leg(payroll.employee_account_id, f"Cuenta interna · {employee.full_name}", "EMPLOYEE", cmd.amount))
    return Plan(command=cmd, payroll=payroll, employee=employee, payment=current, legs=legs)


def create(db: Session, actor: User, cmd: Command, client_request_id: UUID) -> Created:
    fingerprint = idempotency.fingerprint(actor.id, SOURCE, _request(cmd))
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    replayed = _replayed(db, client_request_id, fingerprint)
    if replayed is not None:
        return replayed
    _require_pay_roles(actor)
    payroll = payrolls.lock(db, cmd.payroll_id)  # the internal account, then the payroll
    replayed = _replayed(db, client_request_id, fingerprint)  # the winner of a race committed while we waited
    if replayed is not None:
        db.commit()
        return replayed
    planned = plan(db, actor, cmd, payroll=payroll)
    # Taken now, in id order, before the items' FOR SHARE on them (R8-2's deadlock lesson).
    for item in sorted(cmd.items, key=lambda i: i.account_id):
        lock_account(db, item.account_id)

    payout = EmployeePayout(
        payroll_id=payroll.id,
        payroll_closing_id=cmd.payroll_closing_id,
        employee_id=payroll.employee_id,
        employee_account_id=payroll.employee_account_id,
        business_date=cmd.business_date,
        amount=cmd.amount,
        reference=cmd.reference,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(payout)
            db.flush()
    except IntegrityError as exc:
        winner = _by_request_id(db, client_request_id)
        db.rollback()  # release the locks taken for the insert that lost
        if winner is None:
            raise _rejected(exc) from exc
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "employee_payout_id", winner.id)
        return Created(winner.id, created=False)
    for item in cmd.items:
        db.add(
            EmployeePayoutItem(payout_id=payout.id, method=item.method, account_id=item.account_id, amount=item.amount)
        )
    _flush(db)
    operation = ledger.post_operation(
        db,
        actor=actor,
        type=OPERATION_TYPE,
        legs=[ledger.Leg(leg.account_id, leg.amount) for leg in planned.legs],
        client_request_id=uuid5(NAMESPACE, f"employee-payout:{client_request_id}"),
        business_date=cmd.business_date,
        # No name nor amount owed: whoever reads the operation without the area sees it redacted anyway.
        description=f"Pago de liquidación interna · pago n.º {payout.id}",
        source=ledger.SourceRef(SOURCE, payout.id),
    ).operation
    payout.operation_id = operation.id
    _flush(db)
    record_change(
        db,
        f"{EVENT}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payout.id,
        before=None,
        after=_request(cmd) | {"operation_id": operation.id, "pending_after": money(planned.pending_after)},
    )
    commit(db)
    return Created(payout.id, created=True)


# --- reversing ------------------------------------------------------------------------------------


def reverse(db: Session, actor: User, payout_id: int, reason: str, client_request_id: UUID) -> Created:
    """Undo the whole payout (admin of the area + operator in Rendiciones, with a reason) on its date."""
    if not has_area_access(actor, AREA, "admin"):
        raise AppError(403, "forbidden", "Solo un administrador de Empleados revierte un pago de liquidación.")
    _require_cash_role(actor)
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo del reverso.")
    payroll_id = db.scalar(select(EmployeePayout.payroll_id).where(EmployeePayout.id == payout_id))
    if payroll_id is None:
        raise AppError(404, "employee_payout_not_found", "El pago no existe.")
    payrolls.lock(db, payroll_id)  # the internal account, then the payroll: serializes with its reopening
    payout = db.scalars(
        select(EmployeePayout)
        .where(EmployeePayout.id == payout_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    request_id = uuid5(NAMESPACE, f"employee-payout-reversal:{client_request_id}")
    if payout.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, payout.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (payout.reversed_by, payout.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()  # a retry of the same reversal: release the locks
            return Created(payout.id, created=False)
        raise AppError(
            409,
            "employee_payout_already_reversed",
            "El pago ya fue revertido.",
            {"reversal_operation_id": reversal.id},
        )
    assert payout.operation_id is not None  # a published payout always has it (0029)
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=payout.operation_id,
        source=ledger.SourceRef(SOURCE, payout.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    payout.reversal_operation_id = reversal.id
    payout.reversed_by = actor.id
    payout.reversed_at = datetime.now(UTC)
    payout.reversal_reason = reason
    _flush(db)
    record_change(
        db,
        f"{EVENT}.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payout.id,
        before={"operation_id": payout.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
        },
    )
    commit(db)
    return Created(payout.id, created=True)


# --- shared ---------------------------------------------------------------------------------------


def can_pay(actor: User) -> bool:
    return has_area_access(actor, AREA, "operator") and has_module_access(actor, MODULE_KEY, "operator")


def can_reverse(actor: User) -> bool:
    return has_area_access(actor, AREA, "admin") and has_module_access(actor, MODULE_KEY, "operator")


def get(db: Session, payout_id: int) -> EmployeePayout:
    payout = db.get(EmployeePayout, payout_id)
    if payout is None:
        raise AppError(404, "employee_payout_not_found", "El pago no existe.")
    return payout


def commit(db: Session) -> None:
    """Commit; a rule only the database saw is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise _rejected(exc) from exc


def _flush(db: Session) -> None:
    try:
        db.flush()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise _rejected(exc) from exc


def _rejected(exc: DBAPIError) -> AppError:
    logger.warning("employee_payout_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
    if "payroll_overpaid" in str(exc.orig):
        return AppError(
            409, "payout_exceeds_pending", "Otro pago se registró al mismo tiempo: el pago supera lo pendiente."
        )
    return AppError(
        409,
        "invalid_employee_payout",
        "El pago no cumple las reglas de la cuenta interna y no se registró. Revisá los datos.",
    )


def _request(cmd: Command) -> dict[str, Any]:
    return {
        "payroll_id": cmd.payroll_id,
        "payroll_closing_id": cmd.payroll_closing_id,
        "business_date": cmd.business_date.isoformat(),
        "items": [
            {"method": i.method, "account_id": i.account_id, "amount": money(i.amount)}
            for i in sorted(cmd.items, key=lambda i: i.account_id)
        ],
        "reference": cmd.reference,
    }


def _require_pay_roles(actor: User) -> None:
    if not has_area_access(actor, AREA, "operator"):
        raise AppError(403, "forbidden", "Necesitás el rol operador o administrador de Empleados para pagar.")
    _require_cash_role(actor)


def _require_cash_role(actor: User) -> None:
    """A payout moves a cash box or the bank, so it also needs operator in Rendiciones (ADR 010 D9)."""
    if not has_module_access(actor, MODULE_KEY, "operator"):
        raise AppError(
            403,
            "forbidden",
            "Un pago de liquidación mueve una caja o el banco: necesitás además el rol operador en Rendiciones.",
        )


def _payout_account(db: Session, item: Item) -> ledger.AccountBalance:
    try:
        account = ledger.account_balance(db, item.account_id)
    except AppError:
        account = None
    if account is None or not account.active or account.kind != METHOD_KINDS[item.method]:
        raise AppError(
            422,
            "invalid_payout_account",
            "El efectivo sale de una caja activa y la transferencia de un banco activo.",
            {"account_id": item.account_id, "method": item.method},
        )
    return account


def _replayed(db: Session, client_request_id: UUID, fingerprint: str) -> Created | None:
    existing = _by_request_id(db, client_request_id)
    if existing is None:
        return None
    idempotency.check_replay(existing.request_fingerprint, fingerprint, "employee_payout_id", existing.id)
    return Created(existing.id, created=False)


def _by_request_id(db: Session, client_request_id: UUID) -> EmployeePayout | None:
    return db.scalars(
        select(EmployeePayout)
        .where(EmployeePayout.client_request_id == client_request_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
