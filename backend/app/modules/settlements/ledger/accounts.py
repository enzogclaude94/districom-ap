"""Accounts of the ledger that an admin creates: one contributions account per partner (P3)."""

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
