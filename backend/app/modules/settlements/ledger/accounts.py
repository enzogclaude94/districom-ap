"""Accounts of the ledger that an admin creates: one contributions account per partner (P3)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.ledger.models import Account, account_name_key
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User


def create_owner_account(db: Session, *, actor: User, name: str) -> int:
    """Create the contributions account of a partner (`OWNER_EQUITY`, may be negative) and return its id.

    No two accounts may share a name up to case, accents and spacing, active or not: a partner who
    already has an account is told which one instead of getting a second. The caller commits.
    """
    if not has_module_access(actor, "settlements", "admin"):
        raise AppError(403, "forbidden", "No tenés permiso para crear cuentas.")
    name = " ".join(name.split())
    if not name:
        raise AppError(422, "invalid_account_name", "Ingresá el nombre del socio.")
    existing = db.scalars(select(Account).where(Account.name_key == account_name_key(name))).one_or_none()
    if existing is not None:
        raise _name_taken(existing.id, existing.name, existing.active)

    account = Account(name=name, kind="OWNER_EQUITY", allow_negative=True)
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError as exc:
        # A concurrent request created the same partner first.
        winner = db.scalars(select(Account).where(Account.name_key == account_name_key(name))).one_or_none()
        if winner is None:
            raise
        raise _name_taken(winner.id, winner.name, winner.active) from exc

    record_change(
        db,
        "settlements.account.created",
        action="create",
        user_id=actor.id,
        entity_type="account",
        entity_id=account.id,
        before=None,
        after={"name": account.name, "kind": account.kind, "allow_negative": account.allow_negative},
    )
    return account.id


def _name_taken(account_id: int, name: str, active: bool) -> AppError:
    state = "" if active else " (dada de baja)"
    return AppError(
        409,
        "account_name_taken",
        f"Ya existe la cuenta «{name}»{state}.",
        {"account_id": account_id, "active": active},
    )


def employee_account_id(db: Session, *, employee_id: int, employee_name: str, user_id: int) -> int:
    """The internal account of an employee (`EMPLOYEE`), created on first use (R2-D6).

    At most one per employee (unique index); a concurrent first use returns the account the other
    transaction created. The name carries the employee id, so two employees with the same name never
    collide. Positive balance: the employee owes the company; negative: the company owes the employee.
    """
    existing = db.scalar(select(Account.id).where(Account.employee_id == employee_id))
    if existing is not None:
        return existing
    account = Account(
        name=f"Cuenta interna de {' '.join(employee_name.split())} (#{employee_id})",
        kind="EMPLOYEE",
        allow_negative=True,
        employee_id=employee_id,
    )
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError:
        winner = db.scalar(select(Account.id).where(Account.employee_id == employee_id))
        if winner is None:
            raise
        return winner
    record_change(
        db,
        "settlements.account.created",
        action="create",
        user_id=user_id,
        entity_type="account",
        entity_id=account.id,
        before=None,
        after={"name": account.name, "kind": account.kind, "employee_id": employee_id},
    )
    return account.id


def outlet_advances_account_id(db: Session, *, outlet_id: int, outlet_name: str, user_id: int) -> int:
    """«Entregas a cuenta — {Boca}» (`OUTLET_ADVANCES`), created on first use (R7-3), never seeded.

    At most one per boca (unique index); a concurrent first use returns the account the other transaction
    created. May be negative: cash the boca already handed over to a cash box, not yet consolidated by the close
    of its settlement. If another account already has that name (a boca named like a partner), the boca's id
    tells them apart.
    """
    existing = advances_account_of(db, outlet_id)
    if existing is not None:
        return existing
    name = f"Entregas a cuenta — {' '.join(outlet_name.split())}"
    if db.scalar(select(Account.id).where(Account.name_key == account_name_key(name))) is not None:
        name = f"{name} (#{outlet_id})"
    account = Account(name=name, kind="OUTLET_ADVANCES", allow_negative=True, outlet_id=outlet_id)
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError:
        winner = advances_account_of(db, outlet_id)
        if winner is None:
            raise
        return winner
    record_change(
        db,
        "settlements.account.created",
        action="create",
        user_id=user_id,
        entity_type="account",
        entity_id=account.id,
        before=None,
        after={"name": account.name, "kind": account.kind, "outlet_id": outlet_id},
    )
    return account.id


def advances_account_of(db: Session, outlet_id: int) -> int | None:
    """The «Entregas a cuenta» of a boca, if it was ever used."""
    return db.scalar(select(Account.id).where(Account.outlet_id == outlet_id, Account.kind == "OUTLET_ADVANCES"))


@dataclass(frozen=True)
class CashBox:
    id: int
    name: str


def active_cash_box(db: Session, account_id: int) -> CashBox | None:
    """The account if it is an active cash box (where a boca's remittance may go, R7-3); None otherwise."""
    account = db.get(Account, account_id)
    if account is None or account.kind != "CASH_BOX" or not account.active:
        return None
    return CashBox(account.id, account.name)


def lock_account(db: Session, account_id: int) -> None:
    """Take an account row FOR NO KEY UPDATE: serializes the writers of one account's documents."""
    db.execute(select(Account.id).where(Account.id == account_id).with_for_update(key_share=True))


def employees_of_accounts(db: Session, account_ids: list[int]) -> dict[int, int]:
    """Employee of each internal (`EMPLOYEE`) account among `account_ids`."""
    if not account_ids:
        return {}
    rows = db.execute(
        select(Account.id, Account.employee_id).where(Account.id.in_(account_ids), Account.employee_id.is_not(None))
    )
    return dict(rows.all())  # type: ignore[arg-type]
