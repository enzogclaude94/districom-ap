"""Fixtures of Rendiciones: seeded accounts, actors per role, posting helpers, API logins and drivers."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules, as the app does)
from app.modules.settlements.ledger.models import Account
from app.modules.settlements.ledger.service import Leg, Posted, post_operation
from app.platform.auth.models import User
from app.platform.parties.models import Employee, EmployeeFunction
from tests.conftest import PASSWORD


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


@pytest.fixture
def as_role(client: TestClient, make_user, grant) -> Callable[[str], TestClient]:  # type: ignore[no-untyped-def]
    """Log in as the platform admin ("admin") or as a user with that role in Rendiciones only."""
    created: set[str] = set()

    def _as(role: str) -> TestClient:
        email = f"{role}@districom.test"
        if role not in created:
            make_user(email=email, role="admin" if role == "admin" else "viewer")
            if role in ("viewer", "operator"):
                grant(role, "settlements", role)
            created.add(role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _as


@pytest.fixture
def driver(db: Session) -> Callable[..., int]:
    """An employee holding `settlements.delivery_driver` from `valid_from` (default 2026-01-01) on."""

    def _driver(
        name: str, valid_from: date = date(2026, 1, 1), valid_to: date | None = None, function: str | None = None
    ) -> int:
        employee = Employee(full_name=name)
        db.add(employee)
        db.flush()
        db.add(
            EmployeeFunction(
                employee_id=employee.id,
                function_code=function or "settlements.delivery_driver",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )
        db.commit()
        return employee.id

    return _driver


# --- R7-2: bocas de expendio -----------------------------------------------------------------------


class Roles:
    """The test client is one browser: each property logs it in as that user and returns it."""

    def __init__(self, as_role) -> None:  # type: ignore[no-untyped-def]
        self._as = as_role

    @property
    def admin(self) -> TestClient:
        return self._as("admin")  # type: ignore[no-any-return]

    @property
    def operator(self) -> TestClient:
        return self._as("operator")  # type: ignore[no-any-return]

    @property
    def viewer(self) -> TestClient:
        return self._as("viewer")  # type: ignore[no-any-return]


@pytest.fixture
def r(as_role) -> Roles:  # type: ignore[no-untyped-def]
    return Roles(as_role)


@pytest.fixture
def boca(r: Roles) -> Callable[[str], int]:
    def _boca(code: str) -> int:
        created = r.admin.post("/api/v1/outlets", json={"code": code, "name": f"Boca {code}"})
        assert created.status_code == 201, created.text
        return created.json()["id"]  # type: ignore[no-any-return]

    return _boca


@pytest.fixture
def employee(db: Session) -> Callable[[str], int]:
    def _employee(name: str) -> int:
        row = Employee(full_name=name)
        db.add(row)
        db.commit()
        return row.id

    return _employee


@pytest.fixture
def ids(r: Roles) -> dict[str, int]:
    """Ids of the system accounts by code (and of the bank accounts by name)."""
    return {a["code"] or a["name"]: a["id"] for a in r.admin.get("/api/v1/settlements/accounts").json()}
