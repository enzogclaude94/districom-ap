"""«Banco en descubierto» at the last barrier: `check_account_not_negative` (0013), written to with raw SQL.

The rule the database applies, leg by leg at commit:
- a positive leg (an income) is never checked, so it reduces or cancels an overdraft, whatever is left;
- the debit of a CHEQUE_REJECTION on a BANK account is never checked, so it creates or deepens one;
- any other debit needs every end-of-day balance from its business date on to stay >= 0 after it. A debit
  lowers each of those days by its amount, so this is exactly "it creates no overdraft and deepens none":
  a day left negative was either negative before (deepened) or not (created). Days negative before the
  debit's date are not its business, and incomes never have to cure anything.
"""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.settlements.cash import schemas as cash_schemas
from app.modules.settlements.cash import use_cases as cash
from app.modules.settlements.cheques import cycle
from app.modules.settlements.cheques import schemas as cs
from app.platform.auth.models import User
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.test_cheque_cycle_db_invariants import _account, _deposit, _entry


def credited_on() -> date:
    return days_ago(6)


def spent_on() -> date:
    return days_ago(5)


def rejected_on() -> date:
    return days_ago(4)


@pytest.fixture
def conn(db: Session) -> Iterator[Connection]:
    connection = db.get_bind().connect()  # type: ignore[union-attr]
    yield connection
    connection.rollback()
    connection.close()


def _rejected_cheques(db: Session, actor: Callable[[str], User], ana: int, count: int = 1) -> None:
    """Credited cheques of $100.000 whose money was all withdrawn, then rejected by the bank: the first on
    REJECTED, the next ones today."""
    operator = actor("operator")
    cids = [_entry(db, actor("admin")) for _ in range(count)]
    for cid in cids:
        _deposit(db, operator, cid)
    bank_id = _account(db, "bank")
    credit = cs.CreditIn(
        client_request_id=uuid4(), bank_account_id=bank_id, cheque_ids=cids, business_date=credited_on()
    )
    cycle.credit(db, operator, credit)
    withdrawal = cash_schemas.BankWithdrawalIn(
        client_request_id=uuid4(),
        business_date=spent_on(),
        bank_account_id=bank_id,
        cash_account_id=_account(db, "main_cash"),
        amount=Decimal(100000 * count),
    )
    cash.bank_withdrawal(db, operator, withdrawal)
    for index, cid in enumerate(cids):
        body = cs.RejectionIn(
            client_request_id=uuid4(),
            reason="Sin fondos",
            business_date=rejected_on() if index == 0 else today(),
            responsible_employee_id=ana,
        )
        cycle.reject(db, operator, cid, body)


def _write(conn: Connection, type_: str, on: date, legs: dict[str, str]) -> None:
    """One balanced operation written straight to the tables, as code that skips the service would."""
    user_id = conn.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    op_id = conn.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, client_request_id,"
            " request_fingerprint, created_by, description) VALUES (:t, :d, :r, :f, :u, 'SQL directo') RETURNING id"
        ),
        {"t": type_, "d": on, "r": uuid4(), "f": "0" * 64, "u": user_id},
    ).scalar_one()
    for code, amount in legs.items():
        conn.execute(
            text(
                "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
                "SELECT :o, id, :m FROM settlements.accounts WHERE code = :c"
            ),
            {"o": op_id, "m": amount, "c": code},
        )
    conn.commit()


def income(conn: Connection, amount: str, on: date | None = None) -> None:
    if on is None:
        on = today()
    _write(conn, "ADJUSTMENT", on, {"adjustment": f"-{amount}", "bank": amount})


def debit(conn: Connection, amount: str, on: date | None = None) -> None:
    if on is None:
        on = today()
    _write(conn, "BANK_WITHDRAWAL", on, {"bank": f"-{amount}", "main_cash": amount})


def refused(conn: Connection, write: Callable[[], None]) -> str:
    with pytest.raises(DBAPIError) as excinfo:
        write()
    conn.rollback()
    return str(excinfo.value.orig)


def bank(conn: Connection, on: date | None = None) -> Decimal:
    if on is None:
        on = today()
    return conn.execute(  # type: ignore[no-any-return]
        text(
            "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
            "JOIN settlements.financial_operations o ON o.id = e.operation_id "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.code = 'bank' AND o.business_date <= :d"
        ),
        {"d": on},
    ).scalar_one()


@pytest.fixture
def overdrawn(db: Session, conn: Connection, actor, driver) -> None:  # type: ignore[no-untyped-def]
    _rejected_cheques(db, actor, driver("Ana"))
    conn.rollback()


@pytest.mark.usefixtures("overdrawn")
def test_1_a_rejection_leaves_the_bank_at_minus_100k(conn: Connection) -> None:
    assert bank(conn) == Decimal("-100000.00")
    allow = conn.execute(text("SELECT allow_negative FROM settlements.accounts WHERE code = 'bank'")).scalar_one()
    assert allow is False


@pytest.mark.usefixtures("overdrawn")
def test_2_an_income_of_50k_is_accepted_and_leaves_minus_50k(conn: Connection) -> None:
    income(conn, "50000")
    assert bank(conn) == Decimal("-50000.00")


@pytest.mark.usefixtures("overdrawn")
def test_3_an_income_of_150k_is_accepted_and_ends_positive(conn: Connection) -> None:
    income(conn, "150000")
    assert bank(conn) == Decimal("50000.00")


@pytest.mark.usefixtures("overdrawn")
def test_4_an_ordinary_debit_of_20k_on_minus_100k_is_refused(conn: Connection) -> None:
    message = refused(conn, lambda: debit(conn, "20000"))
    assert "cannot have a negative balance" in message
    assert bank(conn) == Decimal("-100000.00")


@pytest.mark.usefixtures("overdrawn")
def test_5_a_retroactive_debit_deepening_a_negative_day_is_refused(conn: Connection) -> None:
    # Today the bank is back in the black, but the debit is dated inside the overdraft.
    income(conn, "200000")
    assert bank(conn) == Decimal("100000.00")
    message = refused(conn, lambda: debit(conn, "1000", rejected_on()))
    assert "cannot have a negative balance" in message
    # Dated before the overdraft, with the money there that day, it would deepen the later negative days too.
    assert "cannot have a negative balance" in refused(conn, lambda: debit(conn, "1000", credited_on()))
    # Dated after the overdraft ended, within what is there, it is an ordinary debit.
    debit(conn, "1000")
    assert bank(conn) == Decimal("99000.00")


@pytest.mark.usefixtures("overdrawn")
def test_6_a_retroactive_income_that_reduces_but_does_not_end_the_overdraft_is_accepted(conn: Connection) -> None:
    income(conn, "30000", rejected_on() + timedelta(days=1))
    assert bank(conn, rejected_on()) == Decimal("-100000.00")
    assert bank(conn, rejected_on() + timedelta(days=1)) == Decimal("-70000.00")
    assert bank(conn) == Decimal("-70000.00")


def test_7_a_second_rejection_may_deepen_it(db: Session, conn: Connection, actor, driver) -> None:  # type: ignore[no-untyped-def]
    _rejected_cheques(db, actor, driver("Ana"), count=2)
    conn.rollback()
    assert bank(conn, rejected_on()) == Decimal("-100000.00")
    assert bank(conn) == Decimal("-200000.00")


@pytest.mark.usefixtures("overdrawn")
def test_8_raw_sql_cannot_pass_an_ordinary_debit_that_deepens_the_overdraft(conn: Connection) -> None:
    # Any ordinary type, not only a withdrawal: an adjustment, a reversal-like debit, a payment out.
    for type_, counterpart in (("ADJUSTMENT", "adjustment"), ("OWNER_WITHDRAWAL", "opening_balance")):
        message = refused(conn, lambda t=type_, c=counterpart: _write(conn, t, today(), {"bank": "-1", c: "1"}))
        assert "cannot have a negative balance" in message
    # Nor by labelling it a rejection on a non-bank account: the exception is BANK + CHEQUE_REJECTION only.
    _write(conn, "ADJUSTMENT", today(), {"adjustment": "-10", "main_cash": "10"})
    message = refused(conn, lambda: _write(conn, "CHEQUE_REJECTION", today(), {"main_cash": "-20", "adjustment": "20"}))
    assert "negative balance" in message or "no document" in message
    assert bank(conn) == Decimal("-100000.00")
