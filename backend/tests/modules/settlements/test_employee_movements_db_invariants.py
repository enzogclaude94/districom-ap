"""Rules of the employee movements that the database enforces whoever writes (migration 0026), checked with raw SQL
(Enzo's R8-2 point 15). Valid movements are loaded through the API; each case then tries to break one rule directly."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_movement_support import area_admin, internal_account, registered, reverse

FINGERPRINT = "c" * 64


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "500000"),
        (accounts["opening_balance"], "-500000"),
        business_date=days_ago(10),
    )
    return accounts  # type: ignore[no-any-return]


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


@pytest.fixture
def world(
    user_as: Callable[..., TestClient], ana: int, employee: Callable[[str], int], funded, db: Session
) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A consumption, an advance and an adjustment of Ana (published through the API), and Beto with an account."""
    admin = area_admin(user_as)
    beto = employee("Beto")
    registered(admin, "CONSUMPTION", beto, "1")
    return {
        "m_consumption": registered(admin, "CONSUMPTION", ana, "1000"),
        "m_advance": registered(admin, "ADVANCE", ana, "2000", funding_account_id=funded["main_cash"]),
        "m_adjustment": registered(admin, "ADJUSTMENT", ana, "300", direction="CREDIT"),
        "ana": ana,
        "ana_account": internal_account(db, ana),
        "beto": beto,
        "beto_account": internal_account(db, beto),
        "user": _user(db),
        **funded,
    }


def _user(db: Session) -> int:
    db.rollback()
    return int(db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar_one())


def _refused(db: Session, write: Callable[[], None]) -> str:
    def _write_and_commit() -> None:
        write()
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write_and_commit()
    db.rollback()
    return str(caught.value.orig)


def _sql(db: Session, sql: str, **params: object) -> Callable[[], None]:
    return lambda: db.execute(text(sql), params) and None


def _operation(
    db: Session,
    type: str,
    legs: list[tuple[int, str]],
    *,
    source: tuple[str, int] | None = None,
    reverses: int | None = None,
    on: Any = None,
) -> int:
    """Writes an operation and its legs by hand (no commit); returns its id."""
    operation = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
            "(:t, :d, 'Escrito a mano', :k, :f, :st, :si, :rv, (SELECT id FROM users ORDER BY id LIMIT 1)) "
            "RETURNING id"
        ),
        {
            "t": type,
            "d": on or today(),
            "k": uuid4(),
            "f": FINGERPRINT,
            "st": source[0] if source else None,
            "si": source[1] if source else None,
            "rv": reverses,
        },
    ).scalar_one()
    for account_id, amount in legs:
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": operation, "a": account_id, "m": Decimal(amount)},
        )
    return int(operation)


def _movement(db: Session, w: dict[str, Any], **fields: Any) -> int:
    """Inserts a movement by hand (no commit): a consumption of Ana of $500 unless told otherwise."""
    values: dict[str, Any] = {
        "kind": "CONSUMPTION",
        "direction": None,
        "employee_id": w["ana"],
        "employee_account_id": w["ana_account"],
        "business_date": today(),
        "amount": Decimal("500"),
        "employee_amount": Decimal("500"),
        "funding_account_id": None,
        "description": "A mano",
        "client_request_id": uuid4(),
        **fields,
    }
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.employee_movements (kind, direction, employee_id, employee_account_id, "
                "business_date, amount, employee_amount, funding_account_id, description, client_request_id, "
                "request_fingerprint, created_by) VALUES (:kind, :direction, :employee_id, :employee_account_id, "
                ":business_date, :amount, :employee_amount, :funding_account_id, :description, :client_request_id, "
                ":f, (SELECT id FROM users ORDER BY id LIMIT 1)) RETURNING id"
            ),
            values | {"f": FINGERPRINT},
        ).scalar_one()
    )


def _full_consumption(db: Session, w: dict[str, Any], *, employee_leg: str = "500", other: str = "-500", **kw: Any):  # type: ignore[no-untyped-def]
    """A consumption with its operation, written by hand: the control case of several rules."""

    def _write() -> None:
        mid = _movement(db, w)
        op = _operation(
            db,
            "EMPLOYEE_CONSUMPTION",
            [
                (kw.get("employee_account", w["ana_account"]), employee_leg),
                (kw.get("counterpart", w["employee_consumptions"]), other),
            ],
            source=("employee_movement", mid),
        )
        db.execute(
            text("UPDATE settlements.employee_movements SET operation_id = :o WHERE id = :m"), {"o": op, "m": mid}
        )

    return _write


def test_the_control_case_is_accepted(world: dict[str, Any], db: Session) -> None:
    _full_consumption(db, world)()
    db.commit()


def test_checks_on_the_row(world: dict[str, Any], db: Session) -> None:
    w = world
    cases = {
        "kind": {"kind": "LOAN"},
        "amount_positive": {"amount": Decimal("0"), "employee_amount": Decimal("0")},
        "employee_amount": {"employee_amount": Decimal("-500")},
        "direction": {"direction": "CHARGE"},
        "funding": {"funding_account_id": w["main_cash"]},
        "description": {"description": "   "},
    }
    for name, fields in cases.items():
        assert f"ck_employee_movements_{name}" in _refused(db, lambda f=fields: _movement(db, w, **f) and None), name
    # An adjustment needs its direction, an advance its funding account.
    assert "ck_employee_movements_direction" in _refused(db, lambda: _movement(db, w, kind="ADJUSTMENT") and None)
    # (the insert trigger sees it before the check: either refusal is the same barrier)
    refused = _refused(db, lambda: _movement(db, w, kind="ADVANCE") and None)
    assert "ck_employee_movements_funding" in refused or "is not an active cash box or bank" in refused


def test_checks_on_insert(world: dict[str, Any], db: Session) -> None:
    w = world
    assert "is not the internal account of employee" in _refused(
        db, lambda: _movement(db, w, employee_account_id=w["beto_account"]) and None
    )
    assert "is not the internal account of employee" in _refused(
        db, lambda: _movement(db, w, employee_account_id=w["main_cash"]) and None
    )
    assert "is in the future" in _refused(
        db, lambda: _movement(db, w, business_date=today().replace(year=today().year + 1)) and None
    )
    for account in (w["employee_consumptions"], w["opening_balance"], w["beto_account"]):
        assert "is not an active cash box or bank" in _refused(
            db, lambda a=account: _movement(db, w, kind="ADVANCE", funding_account_id=a) and None
        )
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": w["change_cash"]})
    db.commit()
    assert "is not an active cash box or bank" in _refused(
        db, lambda: _movement(db, w, kind="ADVANCE", funding_account_id=w["change_cash"]) and None
    )

    # Born before its operation: a movement inserted with an operation id is refused.
    op = w["m_consumption"]["operation_id"]
    assert "is born in effect, before its operation" in _refused(
        db,
        lambda: (
            db.execute(
                text(
                    "INSERT INTO settlements.employee_movements (kind, employee_id, employee_account_id, "
                    "business_date, amount, employee_amount, description, client_request_id, request_fingerprint, "
                    "created_by, "
                    "operation_id) VALUES ('CONSUMPTION', :e, :a, :d, 1, 1, 'x', :k, :f, :u, :o)"
                ),
                {
                    "e": w["ana"],
                    "a": w["ana_account"],
                    "d": today(),
                    "k": uuid4(),
                    "f": FINGERPRINT,
                    "u": w["user"],
                    "o": op,
                },
            )
            and None
        ),
    )
    # One movement per client request.
    assert "uq_employee_movements_client_request_id" in _refused(
        db,
        lambda: (
            _movement(
                db,
                w,
                client_request_id=db.execute(
                    text("SELECT client_request_id FROM settlements.employee_movements WHERE id = :m"),
                    {"m": w["m_consumption"]["id"]},
                ).scalar_one(),
            )
            and None
        ),
    )


def test_the_operation_matches_its_movement_exactly(world: dict[str, Any], db: Session) -> None:
    w = world
    mismatch = "does not match employee movement"
    assert mismatch in _refused(db, _full_consumption(db, w, employee_leg="400", other="-400"))
    # The leg on another employee's account, or the wrong counterpart.
    assert mismatch in _refused(db, _full_consumption(db, w, employee_account=w["beto_account"]))
    assert "employee_adjustments_closed" in _refused(
        db, _full_consumption(db, w, counterpart=w["employee_adjustments"])
    )
    assert mismatch in _refused(db, _full_consumption(db, w, counterpart=w["main_cash"]))
    # A movement without its operation, an operation without its movement.
    assert "has no operation" in _refused(db, lambda: _movement(db, w) and None)
    assert "has no employee movement" in _refused(
        db,
        lambda: (
            _operation(db, "EMPLOYEE_CONSUMPTION", [(w["ana_account"], "10"), (w["employee_consumptions"], "-10")])
            and None
        ),
    )

    # The operation of another type, or on another date.
    def _other_type() -> None:
        mid = _movement(db, w)
        op = _operation(
            db,
            "EMPLOYEE_ADJUSTMENT",
            [(w["ana_account"], "500"), (w["employee_adjustments"], "-500")],
            source=("employee_movement", mid),
        )
        db.execute(
            text("UPDATE settlements.employee_movements SET operation_id = :o WHERE id = :m"), {"o": op, "m": mid}
        )

    assert "is not the operation of employee movement" in _refused(db, _other_type)

    def _other_date() -> None:
        mid = _movement(db, w)
        op = _operation(
            db,
            "EMPLOYEE_CONSUMPTION",
            [(w["ana_account"], "500"), (w["employee_consumptions"], "-500")],
            source=("employee_movement", mid),
            on=days_ago(1),
        )
        db.execute(
            text("UPDATE settlements.employee_movements SET operation_id = :o WHERE id = :m"), {"o": op, "m": mid}
        )

    assert "is not the operation of employee movement" in _refused(db, _other_date)


def test_reversals_are_exact(world: dict[str, Any], db: Session) -> None:
    w = world
    consumption = w["m_consumption"]
    legs = [(w["ana_account"], "-1000"), (w["employee_consumptions"], "1000")]
    # A reversal without the movement marked reversed by it.
    assert "is reversed exactly by the reversal of its operation" in _refused(
        db, lambda: _operation(db, "REVERSAL", legs, reverses=consumption["operation_id"]) and None
    )

    # Marked reversed on another date than its own.
    def _other_date() -> None:
        reversal = _operation(db, "REVERSAL", legs, reverses=consumption["operation_id"], on=days_ago(1))
        db.execute(
            text(
                "UPDATE settlements.employee_movements SET reversal_operation_id = :r, reversed_by = :u, "
                "reversed_at = now(), reversal_reason = 'x' WHERE id = :m"
            ),
            {"r": reversal, "u": w["user"], "m": consumption["id"]},
        )

    assert "keeps its business date" in _refused(db, _other_date)
    # Marked reversed by an operation that is not its reversal.
    assert "is reversed exactly by the reversal of its operation" in _refused(
        db,
        _sql(
            db,
            "UPDATE settlements.employee_movements SET reversal_operation_id = :r, reversed_by = :u, "
            "reversed_at = now(), reversal_reason = 'x' WHERE id = :m",
            r=w["m_advance"]["operation_id"],
            u=w["user"],
            m=consumption["id"],
        ),
    )


def test_published_and_reversed_movements_never_change_nor_go_away(
    world: dict[str, Any], user_as: Callable[..., TestClient], db: Session
) -> None:
    w = world
    mid = w["m_advance"]["id"]
    for column, value in (
        ("amount", "1"),
        ("employee_amount", "1"),
        ("business_date", f"'{days_ago(1)}'"),
        ("employee_id", str(w["beto"])),
        ("funding_account_id", str(w["bank"])),
        ("description", "'otra'"),
        ("reference", "'otra'"),
        ("kind", "'CONSUMPTION'"),
    ):
        refused = _refused(
            db,
            _sql(db, f"UPDATE settlements.employee_movements SET {column} = {value} WHERE id = :m", m=mid),  # noqa: S608
        )
        assert "is published and never changes" in refused or "ck_employee_movements" in refused, column
    assert "never changes" in _refused(
        db,
        _sql(
            db,
            "UPDATE settlements.employee_movements SET operation_id = :o WHERE id = :m",
            o=w["m_consumption"]["operation_id"],
            m=mid,
        ),
    )
    assert "employee_movements" in _refused(
        db, _sql(db, "DELETE FROM settlements.employee_movements WHERE id = :m", m=mid)
    )
    assert reverse(area_admin(user_as), mid).status_code == 200
    assert "is reversed and does not change" in _refused(
        db, _sql(db, "UPDATE settlements.employee_movements SET reversal_reason = 'otro' WHERE id = :m", m=mid)
    )


def test_the_accounts_are_closed(world: dict[str, Any], db: Session) -> None:
    w = world
    # Only the authorized types move an internal account.
    assert "employee_account_closed" in _refused(
        db, lambda: _operation(db, "CASH_TRANSFER", [(w["ana_account"], "10"), (w["main_cash"], "-10")]) and None
    )
    assert "employee_account_closed" in _refused(
        db, lambda: _operation(db, "ADJUSTMENT", [(w["ana_account"], "10"), (w["adjustment"], "-10")]) and None
    )
    # «Consumos de empleados» only by a consumption, always -X; «Ajustes a empleados» only by an adjustment.
    assert "employee_consumptions_closed" in _refused(
        db, lambda: _operation(db, "ADJUSTMENT", [(w["main_cash"], "10"), (w["employee_consumptions"], "-10")]) and None
    )
    assert "employee_consumptions_closed" in _refused(db, _full_consumption(db, w, employee_leg="-500", other="500"))
    assert "employee_adjustments_closed" in _refused(
        db, lambda: _operation(db, "ADJUSTMENT", [(w["main_cash"], "10"), (w["employee_adjustments"], "-10")]) and None
    )
    # Their kind, code, state and sign never change.
    for code in ("employee_consumptions", "employee_adjustments"):
        for change in ("active = false", "allow_negative = false", "code = 'otra'", "kind = 'EXPENSE'"):
            assert "employee_technical_account_closed" in _refused(
                db,
                _sql(db, f"UPDATE settlements.accounts SET {change} WHERE id = :a", a=w[code]),  # noqa: S608
            ), (code, change)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_downgrade_below_0026_is_refused_while_movements_exist(
    world: dict[str, Any], db: Session, back_at_head: None
) -> None:
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="employee movements exist"):
        command.downgrade(alembic_config(), "0025")
    assert db.execute(text("SELECT count(*) FROM settlements.employee_movements")).scalar_one() == 4
