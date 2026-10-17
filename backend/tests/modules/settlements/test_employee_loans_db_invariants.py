"""Rules of loans and their installments that the database enforces whoever writes (migration 0027), checked with raw
SQL (Enzo's R8-3 point 16, invariants 1-23). Valid loans are given through the API; each case then tries to break one
rule directly. The number in brackets is the invariant of Enzo's list."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
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
from tests.modules.settlements.employee_loan_support import deferred, funded, given, installment, reverse_loan
from tests.modules.settlements.employee_movement_support import area_admin, internal_account, registered

__all__ = ["funded"]

FINGERPRINT = "c" * 64
LATER = date(2027, 6, 30)


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


@pytest.fixture
def world(
    user_as: Callable[..., TestClient], ana: int, employee: Callable[[str], int], funded: dict[str, int], db: Session
) -> dict[str, Any]:
    """A loan of Ana ($3.000 in 3, published through the API) and another one with #2 deferred; Beto with an
    account."""
    admin = area_admin(user_as)
    beto = employee("Beto")
    registered(admin, "CONSUMPTION", beto, "1")
    plain = given(admin, ana, funded["main_cash"], "3000", 3, on=days_ago(2), first_due_on=date(2026, 11, 30))
    other = given(admin, ana, funded["main_cash"], "600", 3, on=days_ago(2), first_due_on=date(2026, 11, 30))
    other = deferred(admin, installment(other, 2)["id"], [(LATER, "200")])
    db.rollback()
    return {
        "loan": plain,
        "deferred": other,
        "ana": ana,
        "ana_account": internal_account(db, ana),
        "beto": beto,
        "beto_account": internal_account(db, beto),
        "user": int(db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar_one()),
        **funded,
    }


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


def _loan(db: Session, w: dict[str, Any], **fields: Any) -> int:
    """Inserts a loan by hand (no commit): $900 of Ana from Caja principal in 3, unless told otherwise."""
    values: dict[str, Any] = {
        "employee_id": w["ana"],
        "employee_account_id": w["ana_account"],
        "business_date": today(),
        "principal": Decimal("900"),
        "funding_account_id": w["main_cash"],
        "installment_count": 3,
        "first_due_on": today(),
        "client_request_id": uuid4(),
        "operation_id": None,
        **fields,
    }
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.employee_loans (employee_id, employee_account_id, business_date, principal, "
                "funding_account_id, installment_count, first_due_on, client_request_id, request_fingerprint, "
                "created_by, operation_id) VALUES (:employee_id, :employee_account_id, :business_date, :principal, "
                ":funding_account_id, :installment_count, :first_due_on, :client_request_id, :f, "
                "(SELECT id FROM users ORDER BY id LIMIT 1), :operation_id) RETURNING id"
            ),
            values | {"f": FINGERPRINT},
        ).scalar_one()
    )


def _installment(
    db: Session, loan_id: int, number: int, amount: str, due_on: date | None = None, deferred_from: int | None = None
) -> int:
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.employee_loan_installments (loan_id, number, due_on, amount, status, "
                "deferred_from_id, created_by) VALUES (:l, :n, :d, :a, 'SCHEDULED', :p, "
                "(SELECT id FROM users ORDER BY id LIMIT 1)) RETURNING id"
            ),
            {"l": loan_id, "n": number, "d": due_on or today(), "a": Decimal(amount), "p": deferred_from},
        ).scalar_one()
    )


def _full_loan(
    db: Session,
    w: dict[str, Any],
    *,
    amounts: tuple[str, ...] = ("300", "300", "300"),
    legs: tuple[str, str] = ("-900", "900"),
    funding: str = "main_cash",
    employee_account: str = "ana_account",
    type: str = "EMPLOYEE_LOAN",
    on: date | None = None,
    **fields: Any,
) -> Callable[[], None]:
    """A $900 loan in 3 with its operation, written by hand: the control case of several rules."""

    def _write() -> None:
        lid = _loan(db, w, **fields)
        for number, amount in enumerate(amounts, start=1):
            _installment(db, lid, number, amount)
        op = _operation(
            db, type, [(w[funding], legs[0]), (w[employee_account], legs[1])], source=("employee_loan", lid), on=on
        )
        db.execute(text("UPDATE settlements.employee_loans SET operation_id = :o WHERE id = :l"), {"o": op, "l": lid})

    return _write


def _inst_id(loan: dict[str, Any], number: int) -> int:
    return int(installment(loan, number)["id"])


def test_the_control_case_is_accepted(world: dict[str, Any], db: Session) -> None:
    _full_loan(db, world)()
    db.commit()


def test_checks_on_the_loan_row(world: dict[str, Any], db: Session) -> None:
    """[1] principal > 0 · [2] a valid installment count · [9] first due date on or after the loan."""
    w = world
    cases = {
        "principal_positive": {"principal": Decimal("0")},
        "installment_count": {"installment_count": 0},
        "first_due_on": {"first_due_on": today() - timedelta(days=1)},
    }
    for name, fields in cases.items():
        assert f"ck_employee_loans_{name}" in _refused(db, lambda f=fields: _loan(db, w, **f) and None), name
    assert "ck_employee_loans_installment_count" in _refused(db, lambda: _loan(db, w, installment_count=121) and None)


def test_checks_on_insert_of_a_loan(world: dict[str, Any], db: Session) -> None:
    """[3] funded by an active cash box or bank · the employee's own account · not in the future · born unpublished."""
    w = world
    assert "is not the internal account of employee" in _refused(
        db, lambda: _loan(db, w, employee_account_id=w["beto_account"]) and None
    )
    future = today() + timedelta(days=1)
    assert "is in the future" in _refused(db, lambda: _loan(db, w, business_date=future, first_due_on=future) and None)
    for account in (w["employee_consumptions"], w["opening_balance"], w["beto_account"], w["cheque_portfolio"]):
        assert "is not an active cash box or bank" in _refused(
            db, lambda a=account: _loan(db, w, funding_account_id=a) and None
        )
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": w["change_cash"]})
    db.commit()
    assert "is not an active cash box or bank" in _refused(
        db, lambda: _loan(db, w, funding_account_id=w["change_cash"]) and None
    )
    assert "is born in effect, before its operation" in _refused(
        db, lambda: _loan(db, w, operation_id=w["loan"]["operation_id"]) and None
    )


def test_the_operation_matches_its_loan_exactly(world: dict[str, Any], db: Session) -> None:
    """[4] the exact operation · [5] EMPLOYEE +principal · [6] the counterpart -principal · [7] document ↔ operation."""
    w = world
    mismatch = "does not match employee loan"
    assert mismatch in _refused(db, _full_loan(db, w, legs=("-800", "800")))
    assert mismatch in _refused(db, _full_loan(db, w, employee_account="beto_account"))
    assert mismatch in _refused(db, _full_loan(db, w, funding="bank"))
    assert "is not the operation of employee loan" in _refused(db, _full_loan(db, w, on=days_ago(1)))
    # A loan without its operation; an operation without its loan.
    assert "has no operation" in _refused(db, lambda: _loan(db, w) and None)
    assert "has no employee loan" in _refused(
        db, lambda: _operation(db, "EMPLOYEE_LOAN", [(w["main_cash"], "-10"), (w["ana_account"], "10")]) and None
    )
    # One operation per loan; its legs only on a cash box or bank and the internal account (the ledger's rule).
    assert "uq_financial_operations_employee_loan" in _refused(
        db,
        lambda: (
            _operation(
                db,
                "EMPLOYEE_LOAN",
                [(w["main_cash"], "-1"), (w["ana_account"], "1")],
                source=("employee_loan", w["loan"]["id"]),
            )
            and None
        ),
    )


def test_the_initial_schedule_adds_up_to_the_loan(world: dict[str, Any], db: Session) -> None:
    """[8] the initial installments are `installment_count`, in order, the first on `first_due_on`, adding up to the
    principal · [16] the active ones add up to the principal."""
    w = world
    wrong = "the initial schedule of employee loan"
    assert wrong in _refused(db, _full_loan(db, w, amounts=("300", "300", "299.99")))
    assert wrong in _refused(db, _full_loan(db, w, amounts=("450", "450")))
    assert wrong in _refused(db, _full_loan(db, w, amounts=("300", "300", "300"), first_due_on=today() + timedelta(1)))

    def _out_of_order() -> None:
        lid = _loan(db, w)
        _installment(db, lid, 1, "300", today() + timedelta(days=30))
        _installment(db, lid, 2, "300", today() + timedelta(days=10))
        _installment(db, lid, 3, "300", today() + timedelta(days=40))
        op = _operation(
            db, "EMPLOYEE_LOAN", [(w["main_cash"], "-900"), (w["ana_account"], "900")], source=("employee_loan", lid)
        )
        db.execute(text("UPDATE settlements.employee_loans SET operation_id = :o WHERE id = :l"), {"o": op, "l": lid})

    assert wrong in _refused(db, _out_of_order)


def test_checks_on_insert_of_an_installment(world: dict[str, Any], db: Session) -> None:
    """[9] due on or after the loan · [10] a valid status · [14] it replaces a deferred one of the same loan · [15] no
    cycles · [17] the next number, unique."""
    w = world
    loan, other = w["loan"], w["deferred"]
    lid = loan["id"]
    before = date.fromisoformat(loan["business_date"]) - timedelta(days=1)
    two = _inst_id(other, 2)
    assert "before the loan" in _refused(db, lambda: _installment(db, other["id"], 5, "1", before, two) and None)
    # Either barrier: the trigger (no such change) or the check (no such status).
    refused = _refused(
        db,
        _sql(
            db, "UPDATE settlements.employee_loan_installments SET status = 'PAID' WHERE id = :i", i=_inst_id(loan, 1)
        ),
    )
    assert "cannot become PAID" in refused or "ck_employee_loan_installments_status" in refused
    # Correlative and never reused.
    assert "is not the next one" in _refused(db, lambda: _installment(db, other["id"], 7, "1", LATER, two) and None)
    assert "is not the next one" in _refused(db, lambda: _installment(db, other["id"], 2, "1", LATER, two) and None)
    # After the loan is published, an installment only replaces a deferred one.
    assert "the initial schedule is written with the loan" in _refused(
        db, lambda: _installment(db, lid, 4, "1", LATER) and None
    )
    # Its origin is DEFERRED and of the same loan.
    assert "replaces a deferred installment of the same loan" in _refused(
        db, lambda: _installment(db, lid, 4, "1", LATER, _inst_id(loan, 1)) and None
    )
    assert "replaces a deferred installment of the same loan" in _refused(
        db, lambda: _installment(db, lid, 4, "1", LATER, two) and None
    )
    # No cycles: the origin is always an earlier installment (never itself nor a later one).
    assert "ck_employee_loan_installments_deferred_from_earlier" in _refused(
        db,
        _sql(
            db,
            "INSERT INTO settlements.employee_loan_installments (id, loan_id, number, due_on, amount, status, "
            "deferred_from_id, created_by) OVERRIDING SYSTEM VALUE VALUES (0, :l, 5, :d, 1, 'SCHEDULED', :p, "
            "(SELECT id FROM users ORDER BY id LIMIT 1))",
            l=other["id"],
            d=LATER,
            p=two,
        ),
    )

    # Numbers are unique within a loan, even past the trigger (switched off here, inside the refused transaction).
    def _duplicate() -> None:
        db.execute(
            text(
                "ALTER TABLE settlements.employee_loan_installments "
                "DISABLE TRIGGER trg_employee_loan_installments_check"
            )
        )
        _installment(db, lid, 1, "1", LATER)

    assert "uq_employee_loan_installments_loan_id_number" in _refused(db, _duplicate)


def test_deferring_is_exact(world: dict[str, Any], db: Session) -> None:
    """[11] only SCHEDULED is deferred · [13] the replacements add up exactly to it · [16] active = principal."""
    w = world
    loan, other = w["loan"], w["deferred"]
    one = _inst_id(loan, 1)
    defer_sql = (
        "UPDATE settlements.employee_loan_installments SET status = 'DEFERRED', deferred_reason = 'x', "
        "deferred_by = :u, deferred_at = now(), deferral_request_id = :k, deferral_fingerprint = :f WHERE id = :i"
    )

    def _defer(i: int, *replacements: str) -> Callable[[], None]:
        def _write() -> None:
            db.execute(text(defer_sql), {"u": w["user"], "k": uuid4(), "f": FINGERPRINT, "i": i})
            for k, amount in enumerate(replacements):
                _installment(db, loan["id"], 4 + k, amount, LATER, i)

        return _write

    db.rollback()
    replaced = "is not replaced by exactly its amount"
    assert replaced in _refused(db, _defer(one))
    assert replaced in _refused(db, _defer(one, "999"))
    assert replaced in _refused(db, _defer(one, "500", "600"))
    _defer(one, "400", "600")()
    db.commit()
    # Only SCHEDULED: deferring a DEFERRED or a CANCELLED installment is refused.
    assert "is DEFERRED and never changes" in _refused(
        db, _sql(db, defer_sql, u=w["user"], k=uuid4(), f=FINGERPRINT, i=_inst_id(other, 2))
    )
    # Deferral fields go together.
    assert "ck_employee_loan_installments_deferral_fields" in _refused(
        db,
        _sql(
            db,
            "UPDATE settlements.employee_loan_installments SET status = 'DEFERRED' WHERE id = :i",
            i=_inst_id(loan, 2),
        ),
    )


def test_installments_never_change_nor_go_away(world: dict[str, Any], db: Session) -> None:
    """[12] DEFERRED is immutable · [18] no DELETE · [19] number, date and amount never change."""
    w = world
    loan, other = w["loan"], w["deferred"]
    scheduled, deferred_ = _inst_id(loan, 1), _inst_id(other, 2)
    for column, value in (("amount", "1"), ("due_on", f"'{LATER}'"), ("number", "9"), ("loan_id", str(other["id"]))):
        assert "never change" in _refused(
            db,
            _sql(
                db,
                f"UPDATE settlements.employee_loan_installments SET {column} = {value} WHERE id = :i",  # noqa: S608
                i=scheduled,
            ),
        ), column
    for change in ("deferred_reason = 'otro'", "amount = 1", "status = 'SCHEDULED'", "due_on = due_on + 1"):
        refused = _refused(
            db,
            _sql(db, f"UPDATE settlements.employee_loan_installments SET {change} WHERE id = :i", i=deferred_),  # noqa: S608
        )
        assert "is DEFERRED and never changes" in refused or "ck_employee_loan_installments" in refused, change
    # A SCHEDULED installment of a loan in effect is not cancelled.
    assert "cannot become CANCELLED" in _refused(
        db,
        _sql(db, "UPDATE settlements.employee_loan_installments SET status = 'CANCELLED' WHERE id = :i", i=scheduled),
    )
    for i in (scheduled, deferred_):
        assert "employee_loan_installments" in _refused(
            db, _sql(db, "DELETE FROM settlements.employee_loan_installments WHERE id = :i", i=i)
        )


def test_published_and_reversed_loans_never_change_nor_go_away(
    world: dict[str, Any], user_as: Callable[..., TestClient], db: Session
) -> None:
    """[19] the financial fields never change · [18] no DELETE · a reversed loan never changes."""
    w = world
    lid = w["loan"]["id"]
    for column, value in (
        ("principal", "1"),
        ("business_date", f"'{days_ago(3)}'"),
        ("employee_id", str(w["beto"])),
        ("funding_account_id", str(w["bank"])),
        ("installment_count", "2"),
        ("first_due_on", f"'{LATER}'"),
        ("description", "'otra'"),
        ("reference", "'otra'"),
    ):
        refused = _refused(
            db,
            _sql(db, f"UPDATE settlements.employee_loans SET {column} = {value} WHERE id = :l", l=lid),  # noqa: S608
        )
        assert "is published and never changes" in refused or "ck_employee_loans" in refused, column
    assert "never changes" in _refused(
        db,
        _sql(
            db,
            "UPDATE settlements.employee_loans SET operation_id = :o WHERE id = :l",
            o=w["deferred"]["operation_id"],
            l=lid,
        ),
    )
    assert "employee_loans" in _refused(db, _sql(db, "DELETE FROM settlements.employee_loans WHERE id = :l", l=lid))
    assert reverse_loan(area_admin(user_as), lid).status_code == 200
    assert "is reversed and does not change" in _refused(
        db, _sql(db, "UPDATE settlements.employee_loans SET reversal_reason = 'otro' WHERE id = :l", l=lid)
    )


def test_reversals_are_exact(world: dict[str, Any], db: Session) -> None:
    """[20] the exact reversal, on the loan's date · [21] after it no installment is SCHEDULED."""
    w = world
    loan = w["loan"]
    on = date.fromisoformat(loan["business_date"])
    legs = [(w["main_cash"], "3000"), (w["ana_account"], "-3000")]
    mark = (
        "UPDATE settlements.employee_loans SET reversal_operation_id = :r, reversed_by = :u, reversed_at = now(), "
        "reversal_reason = 'x' WHERE id = :l"
    )
    cancel = "UPDATE settlements.employee_loan_installments SET status = 'CANCELLED' WHERE loan_id = :l"
    # A reversal without the loan marked reversed by it.
    assert "is reversed exactly by the reversal of its operation" in _refused(
        db, lambda: _operation(db, "REVERSAL", legs, reverses=loan["operation_id"], on=on) and None
    )

    def _reversal(*, on_: date, cancel_installments: bool) -> Callable[[], None]:
        def _write() -> None:
            r = _operation(db, "REVERSAL", legs, reverses=loan["operation_id"], on=on_)
            db.execute(text(mark), {"r": r, "u": w["user"], "l": loan["id"]})
            if cancel_installments:
                db.execute(text(cancel), {"l": loan["id"]})

        return _write

    assert "keeps its business date" in _refused(db, _reversal(on_=on - timedelta(days=1), cancel_installments=True))
    assert "reversed with installments still scheduled" in _refused(db, _reversal(on_=on, cancel_installments=False))
    # Marked reversed by an operation that is not its reversal.
    assert "is reversed exactly by the reversal of its operation" in _refused(
        db, _sql(db, mark, r=w["deferred"]["operation_id"], u=w["user"], l=loan["id"])
    )
    # After a reversal no installment is born.
    _reversal(on_=on, cancel_installments=True)()
    db.commit()
    assert "is reversed" in _refused(
        db, lambda: _installment(db, loan["id"], 4, "1", LATER, _inst_id(loan, 1)) and None
    )


def test_the_internal_account_takes_a_loan(world: dict[str, Any], db: Session) -> None:
    """[22] EMPLOYEE_LOAN is in the internal account's whitelist; the rest stays closed."""
    w = world
    assert "employee_account_closed" in _refused(
        db, lambda: _operation(db, "CASH_TRANSFER", [(w["ana_account"], "10"), (w["main_cash"], "-10")]) and None
    )
    db.rollback()
    found = db.execute(
        text("SELECT count(*) FROM settlements.ledger_entries WHERE operation_id = :o AND account_id = :a"),
        {"o": w["loan"]["operation_id"], "a": w["ana_account"]},
    ).scalar_one()
    assert found == 1


def test_one_loan_and_one_deferral_per_client_request(world: dict[str, Any], db: Session) -> None:
    """[23] idempotency, in the database: a request id names one loan and one deferral."""
    w = world
    db.rollback()
    used = db.execute(
        text("SELECT client_request_id FROM settlements.employee_loans WHERE id = :l"), {"l": w["loan"]["id"]}
    ).scalar_one()
    assert "uq_employee_loans_client_request_id" in _refused(db, lambda: _loan(db, w, client_request_id=used) and None)
    deferral = db.execute(
        text("SELECT deferral_request_id FROM settlements.employee_loan_installments WHERE id = :i"),
        {"i": _inst_id(w["deferred"], 2)},
    ).scalar_one()
    assert "uq_employee_loan_installments_deferral_request_id" in _refused(
        db,
        _sql(
            db,
            "UPDATE settlements.employee_loan_installments SET status = 'DEFERRED', deferred_reason = 'x', "
            "deferred_by = :u, deferred_at = now(), deferral_request_id = :k, deferral_fingerprint = :f "
            "WHERE id = :i",
            u=w["user"],
            k=deferral,
            f=FINGERPRINT,
            i=_inst_id(w["loan"], 1),
        ),
    )


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_downgrade_below_0027_is_refused_while_loans_exist(
    world: dict[str, Any], db: Session, back_at_head: None
) -> None:
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="employee loans exist"):
        command.downgrade(alembic_config(), "0026")
    assert db.execute(text("SELECT count(*) FROM settlements.employee_loans")).scalar_one() == 2
