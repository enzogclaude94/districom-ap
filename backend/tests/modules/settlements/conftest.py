"""Fixtures for the ledger of Rendiciones: seeded accounts, actors per role and posting helpers."""

from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules, as the app does)
from app.modules.settlements.ledger.models import Account
from app.modules.settlements.ledger.service import Leg, Posted, post_operation
from app.platform.auth.models import User


@pytest.fixture
def accounts(db: Session) -> dict[str, int]:
    """Ids of the system accounts by code."""
    return {str(a.code): a.id for a in db.scalars(select(Account).where(Account.code.is_not(None)))}


@pytest.fixture
def actor(db: Session, make_user, grant) -> Callable[[str], User]:  # type: ignore[no-untyped-def]
    """`actor("admin")` is a platform admin; "operator" / "viewer" hold that role in Rendiciones only."""

    users: dict[str, User] = {}

    def _actor(role: str) -> User:
        if role not in users:
            if role == "admin":
                users[role] = make_user(email="admin@districom.test", role="admin")
            else:
                users[role] = make_user(email=f"{role}@districom.test", role="viewer")
                grant(role, "settlements", role)
                db.refresh(users[role])
        return users[role]

    return _actor


@pytest.fixture
def owner_account(db: Session) -> Callable[[str], int]:
    """A partner's contributions account, as an admin will create it in R1-2."""

    def _create(name: str) -> int:
        account = Account(name=name, kind="OWNER_EQUITY", allow_negative=True)
        db.add(account)
        db.commit()
        return account.id

    return _create


@pytest.fixture
def post(db: Session, actor) -> Callable[..., Posted]:  # type: ignore[no-untyped-def]
    """Post and commit an operation as the platform admin (or `by`): `post("CASH_TRANSFER", (a, "-10"), (b, "10"))`."""
    admin = actor("admin")

    def _post(type: str, *legs: tuple[int, str], by: User | None = None, **kwargs: object) -> Posted:
        posted = post_operation(
            db,
            actor=by or admin,
            type=type,
            legs=[Leg(account_id, Decimal(amount)) for account_id, amount in legs],
            client_request_id=kwargs.pop("client_request_id", None) or uuid4(),  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
        db.commit()
        return posted

    return _post
