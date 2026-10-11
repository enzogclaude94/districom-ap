"""The operations service of the ledger against PostgreSQL (ADR 007 §8, Enzo's R1 decisions)."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.ledger.models import Account, FinancialOperation, LedgerEntry
from app.modules.settlements.ledger.service import (
    Leg,
    SourceRef,
    account_balances,
    account_movements,
    get_operation,
    post_operation,
    replace_operation,
    reverse_operation,
)
from app.platform.audit.models import AuditEvent
from tests.modules.settlements.days import days_ago, today


def balances(db: Session, **kwargs) -> dict[str, Decimal]:  # type: ignore[no-untyped-def]
    db.expire_all()
    return {b.name: b.balance for b in account_balances(db, **kwargs)}


def operations_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(FinancialOperation)) or 0


def error_code(excinfo: pytest.ExceptionInfo[AppError]) -> tuple[int, str]:
    return excinfo.value.status_code, excinfo.value.code


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Caja principal $100.000 and Banco $50.000 as opening balance."""
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "100000"),
        (accounts["bank"], "50000"),
        (accounts["opening_balance"], "-150000"),
    )
    return accounts


# --- publishing -----------------------------------------------------------------------------------


def test_opening_balance_and_transfer_derive_balances_from_entries(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    post("CASH_TRANSFER", (funded["main_cash"], "-30000.50"), (funded["change_cash"], "30000.50"))

    assert balances(db) == {
        "Caja principal": Decimal("69999.50"),
        "Caja de cambio": Decimal("30000.50"),
        "Banco": Decimal("50000.00"),
        "Saldo inicial": Decimal("-150000.00"),
        "Ajustes": Decimal("0"),
        "Cobranzas de reparto": Decimal("0"),
        "Gastos operativos": Decimal("0"),
        "Diferencias de rendición": Decimal("0"),
        "Pendientes de cobro": Decimal("0"),
        "Incobrables": Decimal("0"),
        "Cartera de cheques": Decimal("0"),
        "Cheques depositados": Decimal("0"),
        "Pagos por mercadería": Decimal("0"),
        "Devoluciones a terceros": Decimal("0"),
        "Otros pagos": Decimal("0"),
    }
    # The balance is the sum of the entries, and the whole ledger sums zero.
    assert db.scalar(select(func.sum(LedgerEntry.amount))) == 0


def test_operation_keeps_its_legs_author_and_audit(db: Session, post, funded, actor) -> None:  # type: ignore[no-untyped-def]
    posted = post("BANK_DEPOSIT", (funded["main_cash"], "-20000"), (funded["bank"], "20000"), description="Depósito")
    assert posted.created is True

    operation = get_operation(db, posted.operation.id)
    assert operation.type == "BANK_DEPOSIT"
    assert operation.business_date == today()
    assert operation.created_by == actor("admin").id
    assert [(e.account_id, e.amount) for e in operation.entries] == [
        (funded["main_cash"], Decimal("-20000.00")),
        (funded["bank"], Decimal("20000.00")),
    ]
    event = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_id == str(operation.id))
        .where(AuditEvent.event_type == "settlements.operation.posted")
    ).one()
    assert event.user_id == operation.created_by
    assert event.payload["action"] == "post"
    assert event.payload["before"] is None
    assert event.payload["after"]["legs"] == [
        {"account_id": funded["main_cash"], "amount": "-20000.00"},
        {"account_id": funded["bank"], "amount": "20000.00"},
    ]


@pytest.mark.parametrize(
    ("legs", "code"),
    [
        ([("main_cash", "-10"), ("change_cash", "9")], "unbalanced_operation"),
        ([("main_cash", "-10")], "invalid_legs"),
        ([("main_cash", "-10"), ("main_cash", "10")], "invalid_legs"),
        ([("main_cash", "0"), ("change_cash", "0")], "invalid_amount"),
        ([("main_cash", "-10.005"), ("change_cash", "10.005")], "invalid_amount"),
        ([("main_cash", "-1000000000000"), ("change_cash", "1000000000000")], "invalid_amount"),
    ],
)
def test_invalid_legs_are_rejected(db: Session, post, funded, legs, code) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppError) as excinfo:
        post("CASH_TRANSFER", *[(funded[account], amount) for account, amount in legs])
    assert excinfo.value.code == code
    db.rollback()
    assert operations_count(db) == 1  # only the opening balance


@pytest.mark.parametrize(
    ("type", "legs"),
    [
        ("CASH_TRANSFER", [("main_cash", "-10"), ("bank", "10")]),
        ("BANK_DEPOSIT", [("bank", "-10"), ("main_cash", "10")]),
        ("BANK_WITHDRAWAL", [("main_cash", "-10"), ("bank", "10")]),
        ("OPENING_BALANCE", [("main_cash", "10"), ("change_cash", "-10")]),
        ("ADJUSTMENT", [("main_cash", "-10"), ("change_cash", "10")]),
        ("OWNER_CONTRIBUTION", [("main_cash", "10"), ("opening_balance", "-10")]),
    ],
)
def test_legs_must_fit_the_operation_type(post, funded, type, legs) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppError) as excinfo:
        post(type, *[(funded[account], amount) for account, amount in legs], description="motivo")
    assert error_code(excinfo) == (422, "invalid_legs")


def test_unknown_type_and_direct_reversal_are_rejected(post, funded) -> None:  # type: ignore[no-untyped-def]
    for type in ("MAGIC", "REVERSAL"):
        with pytest.raises(AppError) as excinfo:
            post(type, (funded["main_cash"], "-1"), (funded["change_cash"], "1"))
        assert error_code(excinfo) == (422, "unknown_operation_type")


def test_business_date_cannot_be_in_the_future(post, funded) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppError) as excinfo:
        post(
            "CASH_TRANSFER",
            (funded["main_cash"], "-1"),
            (funded["change_cash"], "1"),
            business_date=today() + timedelta(days=1),
        )
    assert error_code(excinfo) == (422, "future_business_date")


def test_inactive_account_cannot_move(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    db.get(Account, funded["change_cash"]).active = False  # type: ignore[union-attr]
    db.commit()
    try:
        with pytest.raises(AppError) as excinfo:
            post("CASH_TRANSFER", (funded["main_cash"], "-1"), (funded["change_cash"], "1"))
        assert error_code(excinfo) == (409, "account_inactive")
    finally:
        db.rollback()
        db.get(Account, funded["change_cash"]).active = True  # type: ignore[union-attr]
        db.commit()


# --- roles ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "type", "allowed"),
    [
        ("viewer", "CASH_TRANSFER", False),
        ("operator", "CASH_TRANSFER", True),
        ("operator", "BANK_DEPOSIT", True),
        ("operator", "ADJUSTMENT", False),
        ("operator", "OPENING_BALANCE", False),
        ("operator", "OWNER_CONTRIBUTION", False),
        ("admin", "ADJUSTMENT", True),
    ],
)
def test_minimum_role_per_operation_type(post, funded, actor, owner_account, role, type, allowed) -> None:  # type: ignore[no-untyped-def]
    legs = {
        "CASH_TRANSFER": [(funded["main_cash"], "-1"), (funded["change_cash"], "1")],
        "BANK_DEPOSIT": [(funded["main_cash"], "-1"), (funded["bank"], "1")],
        "ADJUSTMENT": [(funded["main_cash"], "-1"), (funded["adjustment"], "1")],
        "OPENING_BALANCE": [(funded["change_cash"], "1"), (funded["opening_balance"], "-1")],
        "OWNER_CONTRIBUTION": [(funded["main_cash"], "1"), (owner_account("Socio A"), "-1")],
    }[type]
    user = actor(role)
    if allowed:
        assert post(type, *legs, by=user, description="motivo").created
    else:
        with pytest.raises(AppError) as excinfo:
            post(type, *legs, by=user, description="motivo")
        assert error_code(excinfo) == (403, "forbidden")


def test_user_without_role_in_settlements_cannot_post(db: Session, post, funded, make_user) -> None:  # type: ignore[no-untyped-def]
    outsider = make_user(email="outsider@districom.test", role="viewer")
    with pytest.raises(AppError) as excinfo:
        post("CASH_TRANSFER", (funded["main_cash"], "-1"), (funded["change_cash"], "1"), by=outsider)
    assert error_code(excinfo) == (403, "forbidden")


# --- adjustments (D4) -----------------------------------------------------------------------------


def test_adjustment_requires_a_reason(post, funded) -> None:  # type: ignore[no-untyped-def]
    for description in (None, "   "):
        with pytest.raises(AppError) as excinfo:
            post("ADJUSTMENT", (funded["main_cash"], "-500"), (funded["adjustment"], "500"), description=description)
        assert error_code(excinfo) == (422, "reason_required")


def test_adjustment_is_audited_with_its_reason_and_reversible(db: Session, post, funded, actor) -> None:  # type: ignore[no-untyped-def]
    adjustment = post(
        "ADJUSTMENT", (funded["main_cash"], "-500"), (funded["adjustment"], "500"), description="Arqueo: faltan $500"
    ).operation
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "settlements.operation.posted")).all()[-1]
    assert event.payload["after"]["description"] == "Arqueo: faltan $500"
    assert event.payload["after"]["type"] == "ADJUSTMENT"

    reverse_operation(
        db, actor=actor("admin"), operation_id=adjustment.id, reason="Apareció", client_request_id=uuid4()
    )
    db.commit()
    assert balances(db)["Caja principal"] == Decimal("100000.00")
    assert balances(db)["Ajustes"] == Decimal("0")


# --- funds (D5) -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type", "legs"),
    [
        ("CASH_TRANSFER", [("main_cash", "-100000.01"), ("change_cash", "100000.01")]),
        ("CASH_TRANSFER", [("change_cash", "-1"), ("main_cash", "1")]),
        ("BANK_WITHDRAWAL", [("bank", "-50000.01"), ("main_cash", "50000.01")]),
        ("ADJUSTMENT", [("change_cash", "-1"), ("adjustment", "1")]),
    ],
)
def test_cash_boxes_and_bank_cannot_go_negative(db: Session, post, funded, type, legs) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppError) as excinfo:
        post(type, *[(funded[account], amount) for account, amount in legs], description="motivo")
    assert error_code(excinfo) == (409, "insufficient_funds")
    db.rollback()
    assert operations_count(db) == 1


def test_whole_balance_can_be_moved(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    post("CASH_TRANSFER", (funded["main_cash"], "-100000"), (funded["change_cash"], "100000"))
    assert balances(db)["Caja principal"] == 0


def test_partner_accounts_may_be_negative(db: Session, post, funded, owner_account) -> None:  # type: ignore[no-untyped-def]
    partner = owner_account("Socio A")
    post("OWNER_CONTRIBUTION", (funded["bank"], "70000"), (partner, "-70000"))
    post("OWNER_WITHDRAWAL", (funded["main_cash"], "-10000"), (partner, "10000"))
    assert balances(db)["Socio A"] == Decimal("-60000.00")
    assert balances(db)["Banco"] == Decimal("120000.00")


# --- idempotency (technical adjustment 1) -----------------------------------------------------------


def test_same_request_twice_returns_the_same_operation(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    key = uuid4()
    first = post("CASH_TRANSFER", (funded["main_cash"], "-500"), (funded["change_cash"], "500"), client_request_id=key)
    # Same command: leg order and amount formatting do not matter.
    again = post(
        "CASH_TRANSFER", (funded["change_cash"], "500.00"), (funded["main_cash"], "-500"), client_request_id=key
    )
    assert first.created is True
    assert again.created is False
    assert again.operation.id == first.operation.id
    assert operations_count(db) == 2
    assert balances(db)["Caja de cambio"] == Decimal("500.00")


@pytest.mark.parametrize(
    "change",
    [
        {"legs": [("main_cash", "-700"), ("change_cash", "700")]},
        {"legs": [("main_cash", "-500"), ("bank", "500")], "type": "BANK_DEPOSIT"},
        {"business_days_ago": 1},
        {"description": "otra cosa"},
    ],
)
def test_same_request_id_with_different_content_is_a_conflict(db: Session, post, funded, change) -> None:  # type: ignore[no-untyped-def]
    key = uuid4()
    first = post("CASH_TRANSFER", (funded["main_cash"], "-500"), (funded["change_cash"], "500"), client_request_id=key)
    legs = change.pop("legs", [("main_cash", "-500"), ("change_cash", "500")])
    type = change.pop("type", "CASH_TRANSFER")
    if "business_days_ago" in change:  # resolved now, not when the module was imported
        change["business_date"] = days_ago(change.pop("business_days_ago"))
    with pytest.raises(AppError) as excinfo:
        post(type, *[(funded[a], amount) for a, amount in legs], client_request_id=key, **change)
    assert error_code(excinfo) == (409, "idempotency_conflict")
    assert excinfo.value.details == {"operation_id": first.operation.id}
    db.rollback()
    assert operations_count(db) == 2


def test_same_request_id_from_another_user_is_a_conflict(post, funded, actor) -> None:  # type: ignore[no-untyped-def]
    key = uuid4()
    post("CASH_TRANSFER", (funded["main_cash"], "-500"), (funded["change_cash"], "500"), client_request_id=key)
    with pytest.raises(AppError) as excinfo:
        post(
            "CASH_TRANSFER",
            (funded["main_cash"], "-500"),
            (funded["change_cash"], "500"),
            client_request_id=key,
            by=actor("operator"),
        )
    assert error_code(excinfo) == (409, "idempotency_conflict")


def test_retry_is_answered_even_if_funds_changed_since(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    key = uuid4()
    post("CASH_TRANSFER", (funded["main_cash"], "-100000"), (funded["change_cash"], "100000"), client_request_id=key)
    # Caja principal is empty now; the retry must still get the original operation, not insufficient funds.
    again = post(
        "CASH_TRANSFER", (funded["main_cash"], "-100000"), (funded["change_cash"], "100000"), client_request_id=key
    )
    assert again.created is False


# --- reversals --------------------------------------------------------------------------------------


def test_reversal_restores_balances_and_keeps_the_trail(db: Session, post, funded, actor) -> None:  # type: ignore[no-untyped-def]
    original = post("CASH_TRANSFER", (funded["main_cash"], "-30000"), (funded["change_cash"], "30000")).operation
    reversal = reverse_operation(
        db, actor=actor("admin"), operation_id=original.id, reason="Cargado dos veces", client_request_id=uuid4()
    ).operation
    db.commit()

    assert balances(db)["Caja principal"] == Decimal("100000.00")
    assert balances(db)["Caja de cambio"] == Decimal("0")
    assert reversal.type == "REVERSAL"
    assert reversal.reverses_operation_id == original.id
    assert reversal.description == "Cargado dos veces"
    assert [(e.account_id, e.amount) for e in reversal.entries] == [
        (funded["main_cash"], Decimal("30000.00")),
        (funded["change_cash"], Decimal("-30000.00")),
    ]
    # The original is untouched and both show in the history, linked.
    db.expire_all()
    assert [(e.account_id, e.amount) for e in get_operation(db, original.id).entries] == [
        (funded["main_cash"], Decimal("-30000.00")),
        (funded["change_cash"], Decimal("30000.00")),
    ]
    movements, total = account_movements(db, funded["change_cash"])
    assert total == 2
    assert [(m.operation_id, m.amount, m.reversed_by_operation_id) for m in movements] == [
        (reversal.id, Decimal("-30000.00"), None),
        (original.id, Decimal("30000.00"), reversal.id),
    ]
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "settlements.operation.reversed")).one()
    assert event.payload["after"]["reverses_operation_id"] == original.id


def test_reversal_is_once_idempotent_and_admin_only(db: Session, post, funded, actor) -> None:  # type: ignore[no-untyped-def]
    original = post("CASH_TRANSFER", (funded["main_cash"], "-1"), (funded["change_cash"], "1")).operation
    admin = actor("admin")

    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=actor("operator"), operation_id=original.id, reason="x", client_request_id=uuid4())
    assert error_code(excinfo) == (403, "forbidden")

    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=admin, operation_id=original.id, reason=" ", client_request_id=uuid4())
    assert error_code(excinfo) == (422, "reason_required")

    key = uuid4()
    first = reverse_operation(db, actor=admin, operation_id=original.id, reason="Error", client_request_id=key)
    db.commit()
    retry = reverse_operation(db, actor=admin, operation_id=original.id, reason="Error", client_request_id=key)
    assert (retry.created, retry.operation.id) == (False, first.operation.id)

    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=admin, operation_id=original.id, reason="Error", client_request_id=uuid4())
    assert error_code(excinfo) == (409, "operation_already_reversed")

    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=admin, operation_id=first.operation.id, reason="x", client_request_id=uuid4())
    assert error_code(excinfo) == (409, "cannot_reverse_reversal")

    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=admin, operation_id=999999, reason="x", client_request_id=uuid4())
    assert error_code(excinfo) == (404, "operation_not_found")


def test_reversal_cannot_leave_cash_negative(db: Session, post, funded, actor, owner_account) -> None:  # type: ignore[no-untyped-def]
    partner = owner_account("Socio A")
    contribution = post("OWNER_CONTRIBUTION", (funded["change_cash"], "5000"), (partner, "-5000")).operation
    post("CASH_TRANSFER", (funded["change_cash"], "-4000"), (funded["main_cash"], "4000"))
    with pytest.raises(AppError) as excinfo:
        reverse_operation(db, actor=actor("admin"), operation_id=contribution.id, reason="x", client_request_id=uuid4())
    assert error_code(excinfo) == (409, "insufficient_funds")


# --- reading ----------------------------------------------------------------------------------------


def test_balances_at_a_past_business_date(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    yesterday = today() - timedelta(days=1)
    # The fixture funds Caja principal today; yesterday it needs money of its own.
    post(
        "OPENING_BALANCE",
        (funded["main_cash"], "100"),
        (funded["opening_balance"], "-100"),
        business_date=yesterday - timedelta(days=1),
    )
    post("CASH_TRANSFER", (funded["main_cash"], "-100"), (funded["change_cash"], "100"), business_date=yesterday)
    post("CASH_TRANSFER", (funded["main_cash"], "-50"), (funded["change_cash"], "50"))
    assert balances(db, on=yesterday - timedelta(days=1))["Caja de cambio"] == 0
    assert balances(db, on=yesterday)["Caja de cambio"] == Decimal("100.00")
    assert balances(db)["Caja de cambio"] == Decimal("150.00")


def test_inactive_accounts_are_hidden_unless_asked(db: Session, owner_account) -> None:  # type: ignore[no-untyped-def]
    partner = owner_account("Socio B")
    db.get(Account, partner).active = False  # type: ignore[union-attr]
    db.commit()
    assert "Socio B" not in balances(db)
    assert "Socio B" in balances(db, include_inactive=True)


def test_movements_are_paginated_newest_first(db: Session, post, funded) -> None:  # type: ignore[no-untyped-def]
    ids = [
        post("CASH_TRANSFER", (funded["main_cash"], f"-{n}"), (funded["change_cash"], f"{n}")).operation.id
        for n in (1, 2, 3)
    ]
    page, total = account_movements(db, funded["change_cash"], limit=2, offset=0)
    assert total == 3
    assert [m.operation_id for m in page] == [ids[2], ids[1]]
    page, _ = account_movements(db, funded["change_cash"], limit=2, offset=2)
    assert [m.operation_id for m in page] == [ids[0]]

    with pytest.raises(AppError) as excinfo:
        account_movements(db, 999999)
    assert error_code(excinfo) == (404, "account_not_found")


def test_post_without_commit_writes_nothing(db: Session, funded, actor) -> None:  # type: ignore[no-untyped-def]
    """The service never commits: the caller's transaction decides (a document and its operation go together)."""
    post_operation(
        db,
        actor=actor("admin"),
        type="CASH_TRANSFER",
        legs=[Leg(funded["main_cash"], Decimal("-1")), Leg(funded["change_cash"], Decimal("1"))],
        client_request_id=uuid4(),
    )
    db.rollback()
    assert operations_count(db) == 1


# --- historical balances (Enzo, review of R1-1) ------------------------------------------------------


@pytest.fixture
def history(db: Session, post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Enzo's example on Caja principal: day 1 +100.000, day 2 -80.000, day 3 +200.000 (today: 220.000)."""
    day1, day2, day3 = (today() - timedelta(days=n) for n in (3, 2, 1))
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "100000"),
        (accounts["opening_balance"], "-100000"),
        business_date=day1,
    )
    post("CASH_TRANSFER", (accounts["main_cash"], "-80000"), (accounts["change_cash"], "80000"), business_date=day2)
    post(
        "ADJUSTMENT",
        (accounts["main_cash"], "200000"),
        (accounts["adjustment"], "-200000"),
        business_date=day3,
        description="Fondos",
    )
    return accounts


def test_retroactive_withdrawal_cannot_leave_a_past_day_negative(db: Session, post, history) -> None:  # type: ignore[no-untyped-def]
    day2 = today() - timedelta(days=2)
    assert balances(db)["Caja principal"] == Decimal("220000.00")
    with pytest.raises(AppError) as excinfo:
        post("CASH_TRANSFER", (history["main_cash"], "-50000"), (history["change_cash"], "50000"), business_date=day2)
    assert error_code(excinfo) == (409, "insufficient_funds")
    assert excinfo.value.details["business_date"] == day2.isoformat()
    assert excinfo.value.details["balance"] == "20000.00"
    assert day2.strftime("%d/%m/%Y") in excinfo.value.message
    db.rollback()
    assert balances(db, on=day2)["Caja principal"] == Decimal("20000.00")


def test_retroactive_withdrawal_that_fits_every_later_day_is_accepted(db: Session, post, history) -> None:  # type: ignore[no-untyped-def]
    day2 = today() - timedelta(days=2)
    post("CASH_TRANSFER", (history["main_cash"], "-20000"), (history["change_cash"], "20000"), business_date=day2)
    assert balances(db, on=day2)["Caja principal"] == 0
    assert balances(db)["Caja principal"] == Decimal("200000.00")


def test_withdrawal_dated_before_the_first_deposit_is_rejected(post, history) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppError) as excinfo:
        post(
            "CASH_TRANSFER",
            (history["main_cash"], "-1"),
            (history["change_cash"], "1"),
            business_date=today() - timedelta(days=10),
        )
    assert error_code(excinfo) == (409, "insufficient_funds")


def test_a_later_day_is_protected_too(post, history) -> None:  # type: ignore[no-untyped-def]
    """Day 1 has 100.000, but day 2 drops to 20.000: a 30.000 withdrawal on day 1 breaks day 2."""
    with pytest.raises(AppError) as excinfo:
        post(
            "CASH_TRANSFER",
            (history["main_cash"], "-30000"),
            (history["change_cash"], "30000"),
            business_date=today() - timedelta(days=3),
        )
    assert excinfo.value.details["business_date"] == (today() - timedelta(days=2)).isoformat()


def test_balance_is_checked_at_the_end_of_the_business_day(db: Session, post, accounts) -> None:  # type: ignore[no-untyped-def]
    """Within a day there is no order between operations: what counts is each day's closing balance."""
    day1, day2 = today() - timedelta(days=2), today() - timedelta(days=1)
    post(
        "OPENING_BALANCE",
        (accounts["change_cash"], "100"),
        (accounts["main_cash"], "50"),
        (accounts["opening_balance"], "-150"),
        business_date=day1,
    )
    post("CASH_TRANSFER", (accounts["main_cash"], "-50"), (accounts["change_cash"], "50"), business_date=day2)
    post("CASH_TRANSFER", (accounts["change_cash"], "-120"), (accounts["main_cash"], "120"), business_date=day2)
    # Day 2 closes at 30: another 30 out on day 2 leaves it at 0 and is accepted; one more peso is not.
    post("CASH_TRANSFER", (accounts["change_cash"], "-30"), (accounts["main_cash"], "30"), business_date=day2)
    assert balances(db, on=day2)["Caja de cambio"] == 0
    with pytest.raises(AppError) as excinfo:
        post("CASH_TRANSFER", (accounts["change_cash"], "-0.01"), (accounts["main_cash"], "0.01"), business_date=day1)
    assert excinfo.value.details["business_date"] == day2.isoformat()


def test_retroactive_reversal_is_checked_too(db: Session, post, history, actor) -> None:  # type: ignore[no-untyped-def]
    """Undoing the day-1 opening with a reversal dated day 1 would leave day 2 at -80.000."""
    opening = db.scalars(select(FinancialOperation).where(FinancialOperation.type == "OPENING_BALANCE")).one()
    with pytest.raises(AppError) as excinfo:
        reverse_operation(
            db,
            actor=actor("admin"),
            operation_id=opening.id,
            reason="Error de carga",
            client_request_id=uuid4(),
            business_date=today() - timedelta(days=3),
        )
    assert error_code(excinfo) == (409, "insufficient_funds")


# --- replace_operation (R2-2) ---------------------------------------------------------------------


def _close(post, accounts, amount: str = "1000", source_id: int = 1, **kwargs):  # type: ignore[no-untyped-def]
    return post(
        "SETTLEMENT_CLOSE",
        (accounts["main_cash"], amount),
        (accounts["delivery_collections"], f"-{amount}"),
        source=SourceRef("settlement", source_id),
        **kwargs,
    )


def _replace(db: Session, actor, original: int, accounts, amount: str, **kwargs):  # type: ignore[no-untyped-def]
    return replace_operation(
        db,
        actor=actor,
        original_id=original,
        type=kwargs.pop("type", "SETTLEMENT_CLOSE"),
        legs=[Leg(accounts["main_cash"], Decimal(amount)), Leg(accounts["delivery_collections"], -Decimal(amount))],
        client_request_id=kwargs.pop("client_request_id", None) or uuid4(),
        reversal_request_id=kwargs.pop("reversal_request_id", None) or uuid4(),
        business_date=today() - timedelta(days=1),
        description="Cierre corregido",
        reason="Corrección",
        source=kwargs.pop("source", SourceRef("settlement", 1)),
    )


def test_replace_publishes_reversal_and_replacement_once(db: Session, post, accounts, actor) -> None:  # type: ignore[no-untyped-def]
    original = _close(post, accounts, business_date=today() - timedelta(days=1)).operation
    key, reversal_key = uuid4(), uuid4()
    first = _replace(
        db, actor("operator"), original.id, accounts, "1500", client_request_id=key, reversal_request_id=reversal_key
    )
    db.commit()
    again = _replace(
        db, actor("operator"), original.id, accounts, "1500", client_request_id=key, reversal_request_id=reversal_key
    )
    assert (first.created, again.created) == (True, False)
    assert (again.reversal.id, again.replacement.id) == (first.reversal.id, first.replacement.id)
    assert first.reversal.reverses_operation_id == original.id
    assert first.reversal.business_date == first.replacement.business_date == original.business_date
    assert balances(db)["Caja principal"] == Decimal("1500.00")
    with pytest.raises(AppError) as conflict:
        _replace(db, actor("operator"), original.id, accounts, "1600", client_request_id=key)
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(AppError) as twice:
        _replace(db, actor("operator"), original.id, accounts, "1600")
    assert twice.value.code == "operation_already_reversed"


def test_replace_only_documents_of_the_same_source(db: Session, post, accounts, funded, actor) -> None:  # type: ignore[no-untyped-def]
    original = _close(post, accounts).operation
    transfer = post("CASH_TRANSFER", (accounts["main_cash"], "-10"), (accounts["change_cash"], "10")).operation
    operator = actor("operator")
    cases = [
        ({"type": "CASH_TRANSFER"}, original.id, "unknown_operation_type"),
        ({"source": SourceRef("settlement", 2)}, original.id, "invalid_replacement"),
        ({}, transfer.id, "invalid_replacement"),
        ({}, 999999, "operation_not_found"),
    ]
    for kwargs, original_id, code in cases:
        with pytest.raises(AppError) as caught:
            _replace(db, operator, original_id, accounts, "10", **kwargs)
        assert caught.value.code == code, kwargs
        db.rollback()


def test_a_document_operation_is_not_reversed_directly(db: Session, post, accounts, actor) -> None:  # type: ignore[no-untyped-def]
    original = _close(post, accounts).operation
    with pytest.raises(AppError) as caught:
        reverse_operation(db, actor=actor("admin"), operation_id=original.id, reason="x", client_request_id=uuid4())
    assert caught.value.code == "operation_owned_by_document"


def test_replace_checks_the_net_effect_only(db: Session, post, accounts, actor) -> None:  # type: ignore[no-untyped-def]
    """$1.000 closed yesterday and moved out today. Replacing it by $1.200 only needs room for the net +$200;
    replacing it by $800 is a net -$200 that would leave the box at -$200 from today on."""
    _close(post, accounts, business_date=today() - timedelta(days=1))
    original = db.scalars(select(FinancialOperation)).one()
    post("CASH_TRANSFER", (accounts["main_cash"], "-1000"), (accounts["change_cash"], "1000"))
    operator = actor("operator")
    with pytest.raises(AppError) as caught:
        _replace(db, operator, original.id, accounts, "800")
    assert caught.value.code == "insufficient_funds"
    db.rollback()
    _replace(db, operator, original.id, accounts, "1200")
    db.commit()
    assert balances(db)["Caja principal"] == Decimal("200.00")
