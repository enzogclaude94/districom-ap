"""Cheques of the portfolio delivered as a means of payment (R6-2) over HTTP: one or several cheques, mixed with
cash and the bank, the exact legs, the whole face amount, the portfolio on the payment's date, the derived
DELIVERED state and its history, the reversal with every cheque recovered (never a partial one), idempotency
and permissions. Database rules are in `test_payment_cheques_db_invariants.py`, races in
`test_payment_cheques_concurrency.py`."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today
from tests.modules.settlements.direct_support import BASE, account_id, balance, error, fund
from tests.modules.settlements.payment_support import cash, counts, paid, pay, reverse_payment, third_party, transfer
from tests.modules.settlements.test_cheque_cycle_api import (
    assert_cheque_accounts_invariant,
    cheque,
    credited,
    deposit,
    deposited,
    entered,
    give_back,
    reverse,
)
from tests.modules.settlements.test_cheques_api import bank as issuer
from tests.modules.settlements.test_cheques_api import create, new, physical


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
def box(r: Roles) -> int:
    fund(r.admin, "main_cash", "500000", today() - timedelta(days=10))
    return account_id(r.admin, "main_cash")


@pytest.fixture
def bank(r: Roles) -> int:
    fund(r.admin, "bank", "1000000", today() - timedelta(days=10))
    return account_id(r.admin, "bank")


@pytest.fixture
def perez(r: Roles) -> int:
    return third_party(r.admin)


@pytest.fixture
def ana(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Ana Cobranzas")  # type: ignore[no-any-return]


def by_cheque(cid: int, amount: str, reference: str | None = None) -> dict[str, Any]:
    return {"method": "CHEQUE", "cheque_id": cid, "amount": amount, "reference": reference}


def legs(db: Session, operation_id: int) -> list[tuple[str, int | None, Decimal]]:
    rows = db.execute(
        text(
            "SELECT a.code, e.cheque_id, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :op "
            "ORDER BY e.amount, e.cheque_id"
        ),
        {"op": operation_id},
    ).all()
    return [(code, cid, amount) for code, cid, amount in rows]


def events(db: Session, cid: int, event_type: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT e.payment_id, e.operation_id, e.reason, e.after, u.email, e.created_at "
            "FROM settlements.cheque_events e JOIN users u ON u.id = e.created_by "
            "WHERE e.cheque_id = :c AND e.event_type = :t ORDER BY e.id"
        ),
        {"c": cid, "t": event_type},
    ).mappings()
    return [dict(row) for row in rows]


# --- delivering cheques ---------------------------------------------------------------------------------


def test_a_single_cheque_pays_a_supplier(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "200000")
    payment = paid(r.operator, perez, [by_cheque(a, "200000", "Endoso")], amount="200000")
    assert (payment["state"], payment["amount"]) == ("IN_EFFECT", "200000.00")
    (item,) = payment["items"]
    assert (item["method"], item["cheque_id"], item["account_id"], item["amount"]) == ("CHEQUE", a, None, "200000.00")
    assert (item["cheque"]["state"], item["cheque"]["state_label"]) == ("DELIVERED", "Entregado")
    assert legs(db, payment["operation_id"]) == [
        ("cheque_portfolio", a, Decimal("-200000.00")),
        ("merchandise_payments", None, Decimal("200000.00")),
    ]
    now = cheque(r.admin, a)
    assert (now["state"], now["state_label"], now["location_label"]) == (
        "DELIVERED",
        "Entregado",
        "Entregado a Distribuidora Pérez S.A.",
    )
    assert (now["delivery_payment_id"], now["delivery_third_party_id"], now["delivery_business_date"]) == (
        payment["id"],
        perez,
        today().isoformat(),
    )
    assert balance(r.admin, "cheque_portfolio") == Decimal(0)
    assert_cheque_accounts_invariant(r.admin, db)


def test_two_cheques_in_one_payment(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "200000"), entered(r.admin, "150000")
    payment = paid(r.operator, perez, [by_cheque(a, "200000"), by_cheque(b, "150000")], concept="REFUND")
    assert payment["amount"] == "350000.00"
    assert legs(db, payment["operation_id"]) == [
        ("cheque_portfolio", a, Decimal("-200000.00")),
        ("cheque_portfolio", b, Decimal("-150000.00")),
        ("third_party_refunds", None, Decimal("350000.00")),
    ]
    assert {cheque(r.admin, cid)["state"] for cid in (a, b)} == {"DELIVERED"}


def test_cash_and_a_cheque(r, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "200000")
    payment = paid(r.operator, perez, [cash(box, "50000"), by_cheque(a, "200000")], amount="250000")
    assert legs(db, payment["operation_id"]) == [
        ("cheque_portfolio", a, Decimal("-200000.00")),
        ("main_cash", None, Decimal("-50000.00")),
        ("merchandise_payments", None, Decimal("250000.00")),
    ]
    assert balance(r.admin, "main_cash") == Decimal("450000.00")


def test_the_bank_and_a_cheque(r, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "200000")
    payment = paid(r.operator, perez, [transfer(bank, "300000", "TRF 77"), by_cheque(a, "200000")])
    assert legs(db, payment["operation_id"]) == [
        ("bank", None, Decimal("-300000.00")),
        ("cheque_portfolio", a, Decimal("-200000.00")),
        ("merchandise_payments", None, Decimal("500000.00")),
    ]


def test_cash_bank_and_two_cheques_balance_to_zero(r, box, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Enzo's example: $500.000 paid with $100.000 cash, $100.000 bank and two cheques of $200.000 and $100.000."""
    a, b = entered(r.admin, "200000"), entered(r.admin, "100000")
    items = [cash(box, "100000"), transfer(bank, "100000"), by_cheque(a, "200000"), by_cheque(b, "100000")]
    payment = paid(r.operator, perez, items, amount="500000")
    found = legs(db, payment["operation_id"])
    assert sum(amount for _, _, amount in found) == 0
    assert sorted(found, key=str) == sorted(
        [
            ("cheque_portfolio", a, Decimal("-200000.00")),
            ("cheque_portfolio", b, Decimal("-100000.00")),
            ("bank", None, Decimal("-100000.00")),
            ("main_cash", None, Decimal("-100000.00")),
            ("merchandise_payments", None, Decimal("500000.00")),
        ],
        key=str,
    )
    assert [i["method"] for i in payment["items"]] == ["CASH", "TRANSFER", "CHEQUE", "CHEQUE"]
    assert_cheque_accounts_invariant(r.admin, db)


# --- the whole face amount --------------------------------------------------------------------------------


def test_a_cheque_is_delivered_for_its_whole_amount(r, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "200000")
    before = counts(db)
    response = pay(r.operator, perez, [by_cheque(a, "150000")])
    assert error(response) == (422, "cheque_amount_mismatch")
    assert response.json()["details"] == {"cheque_id": a, "cheque_amount": "200000.00", "amount": "150000.00"}
    assert "$ 200.000,00" in response.json()["message"]
    # Paying $150.000 with it is impossible: no change given back, no credit balance.
    assert error(pay(r.operator, perez, [by_cheque(a, "200000"), cash(box, "-50000")])) == (422, "validation_error")
    assert error(pay(r.operator, perez, [by_cheque(a, "200000")], amount="150000")) == (422, "payment_amount_mismatch")
    assert counts(db) == before
    assert cheque(r.admin, a)["state"] == "IN_PORTFOLIO"


def test_a_cheque_item_names_one_cheque_once(r, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "200000")
    before = counts(db)
    twice = [by_cheque(a, "200000"), by_cheque(a, "200000")]
    assert error(pay(r.operator, perez, twice)) == (422, "duplicate_payment_cheque")
    with_account = {**by_cheque(a, "200000"), "account_id": box}
    assert error(pay(r.operator, perez, [with_account])) == (422, "invalid_payment_item")
    assert error(pay(r.operator, perez, [{"method": "CHEQUE", "amount": "200000"}])) == (422, "invalid_payment_item")
    assert error(pay(r.operator, perez, [{**cash(box, "100"), "cheque_id": a}])) == (422, "invalid_payment_account")
    assert error(pay(r.operator, perez, [by_cheque(999999, "200000")])) == (404, "cheque_not_found")
    assert counts(db) == before


# --- only a cheque of the portfolio, on the payment's date ------------------------------------------------


def test_a_deposited_cheque_is_not_delivered(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    deposited(r.admin, [a])
    before = counts(db)
    assert error(pay(r.operator, perez, [by_cheque(a, "100000")])) == (409, "cheque_not_in_portfolio")
    assert counts(db) == before


def test_a_returned_cheque_is_not_delivered(r, perez, ana) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    assert give_back(r.admin, a, today(), ana).status_code == 201
    assert error(pay(r.operator, perez, [by_cheque(a, "100000")])) == (409, "cheque_not_in_portfolio")


def test_a_cheque_not_yet_published_is_not_delivered(r, perez, ana) -> None:  # type: ignore[no-untyped-def]
    """Received in a settlement still open: it is not in the portfolio until the close."""
    sid = create(r.admin, ana, "50000")
    cid = new(r.admin, sid, physical(issuer(r.admin)))
    assert cheque(r.admin, cid)["state"] == "RECEIVED"
    assert error(pay(r.operator, perez, [by_cheque(cid, "50000")])) == (409, "cheque_not_in_portfolio")


def test_a_cheque_received_after_the_payment_date_is_refused(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Enzo's example: a payment dated the 5th with a cheque received on the 7th."""
    a = entered(r.admin, "100000", on=today() - timedelta(days=3))
    before = counts(db)
    response = pay(r.operator, perez, [by_cheque(a, "100000")], on=today() - timedelta(days=5))
    assert error(response) == (422, "cheque_not_in_portfolio_on_date")
    assert response.json()["details"]["in_portfolio_since"] == (today() - timedelta(days=3)).isoformat()
    assert counts(db) == before
    # From the day it entered on, it is fine.
    assert pay(r.operator, perez, [by_cheque(a, "100000")], on=today() - timedelta(days=3)).status_code == 201


def test_the_portfolio_is_continuous_since_the_payment_date(r, perez) -> None:  # type: ignore[no-untyped-def]
    """A deposit reversed (it was wrong) never took the cheque out: the payment may be dated before it."""
    a = entered(r.admin, "100000")
    slip = deposited(r.admin, [a], on=today() - timedelta(days=8))
    assert reverse(r.admin, f"cheque-deposits/{slip['id']}").status_code == 201
    assert pay(r.operator, perez, [by_cheque(a, "100000")], on=today() - timedelta(days=9)).status_code == 201


def test_a_delivered_cheque_cannot_be_deposited_returned_nor_delivered_again(r, perez, ana) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    paid(r.operator, perez, [by_cheque(a, "100000")])
    assert error(deposit(r.admin, [a])) == (409, "cheque_not_in_portfolio")
    assert error(give_back(r.admin, a, today(), ana)) == (409, "cheque_not_in_portfolio")
    assert error(pay(r.operator, perez, [by_cheque(a, "100000")])) == (409, "cheque_not_in_portfolio")


# --- the delivered state and its history ------------------------------------------------------------------


def test_delivered_is_derived_listed_and_in_the_history(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "100000"), entered(r.admin, "50000")
    payment = paid(r.operator, perez, [by_cheque(a, "100000")])
    listed = r.admin.get(f"{BASE}/cheques", params={"state": "DELIVERED"}).json()
    assert [c["id"] for c in listed["items"]] == [a]
    in_portfolio = r.admin.get(f"{BASE}/cheques", params={"in_portfolio": True}).json()
    assert [c["id"] for c in in_portfolio["items"]] == [b]
    summary = r.admin.get(f"{BASE}/cheques/summary").json()
    assert (summary["portfolio_count"], Decimal(summary["portfolio_amount"])) == (1, Decimal(50000))

    (delivered,) = events(db, a, "DELIVERED")
    assert (delivered["payment_id"], delivered["operation_id"], delivered["email"]) == (
        payment["id"],
        payment["operation_id"],
        "operator@districom.test",
    )
    assert delivered["after"] == {
        "payment_id": payment["id"],
        "third_party_id": perez,
        "third_party_name": "Distribuidora Pérez S.A.",
        "business_date": today().isoformat(),
        "operation_id": payment["operation_id"],
    }
    detail = cheque(r.admin, a)
    assert [e["event_type"] for e in detail["events"]][-1] == "DELIVERED"
    assert detail["events"][-1]["payment_id"] == payment["id"]
    (document,) = [d for d in detail["documents"] if d["type"] == "PAYMENT"]
    assert (document["id"], document["business_date"], document["third_party_name"], document["reversed"]) == (
        payment["id"],
        today().isoformat(),
        "Distribuidora Pérez S.A.",
        False,
    )
    assert document["operation_id"] == payment["operation_id"]


# --- reversal: only with every cheque physically recovered -----------------------------------------------


def _state(db: Session) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        db.execute(text(f"SELECT count(*) FROM settlements.{table}")).scalar_one()  # noqa: S608
        for table in ("financial_operations", "ledger_entries", "cheque_events")
    )


def test_a_payment_with_cheques_is_not_reversed_without_the_recovery(r, box, perez, db) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    payment = paid(r.operator, perez, [cash(box, "1000"), by_cheque(a, "100000")])
    before = _state(db)
    response = reverse_payment(r.admin, payment["id"])
    assert error(response) == (409, "cheque_recovery_required")
    assert response.json()["details"] == {"missing_cheque_ids": [a]}
    assert _state(db) == before
    assert cheque(r.admin, a)["state"] == "DELIVERED"
    assert r.admin.get(f"{BASE}/payments/{payment['id']}").json()["state"] == "IN_EFFECT"


def test_no_partial_reversal_when_one_of_two_cheques_was_not_recovered(r, box, perez, db) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "100000"), entered(r.admin, "60000")
    payment = paid(r.operator, perez, [cash(box, "1000"), by_cheque(a, "100000"), by_cheque(b, "60000")])
    before = (_state(db), balance(r.admin, "main_cash"))
    response = reverse_payment(r.admin, payment["id"], recovered_cheque_ids=[a])
    assert error(response) == (409, "cheque_recovery_required")
    assert response.json()["details"] == {"missing_cheque_ids": [b]}
    assert (_state(db), balance(r.admin, "main_cash")) == before
    assert {cheque(r.admin, cid)["state"] for cid in (a, b)} == {"DELIVERED"}


def test_with_every_cheque_recovered_the_payment_is_reversed_whole(r, box, perez, db) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "100000"), entered(r.admin, "60000")
    payment = paid(r.operator, perez, [cash(box, "1000"), by_cheque(a, "100000"), by_cheque(b, "60000")])
    response = reverse_payment(r.admin, payment["id"], "Se pagó al tercero equivocado", recovered_cheque_ids=[b, a])
    assert response.status_code == 201, response.text
    done = response.json()
    assert done["state"] == "REVERSED"
    assert legs(db, done["reversal_operation_id"]) == [
        ("merchandise_payments", None, Decimal("-161000.00")),
        ("main_cash", None, Decimal("1000.00")),
        ("cheque_portfolio", b, Decimal("60000.00")),
        ("cheque_portfolio", a, Decimal("100000.00")),
    ]
    assert balance(r.admin, "main_cash") == Decimal("500000.00")
    assert balance(r.admin, "cheque_portfolio") == Decimal("160000.00")
    for cid in (a, b):
        now = cheque(r.admin, cid)
        assert (now["state"], now["location_label"], now["delivery_payment_id"]) == (
            "IN_PORTFOLIO",
            "Cartera de cheques",
            None,
        )
        (recovered,) = events(db, cid, "RECOVERED_FROM_PAYMENT")
        assert (recovered["payment_id"], recovered["operation_id"], recovered["reason"], recovered["email"]) == (
            payment["id"],
            done["reversal_operation_id"],
            "Se pagó al tercero equivocado",
            "admin@districom.test",
        )
        assert recovered["after"]["reversal_operation_id"] == done["reversal_operation_id"]
        assert recovered["after"]["recovery_confirmed"] is True
        assert recovered["created_at"] is not None
        (document,) = [d for d in now["documents"] if d["type"] == "PAYMENT"]
        assert (document["reversed"], document["reversal_operation_id"]) == (True, done["reversal_operation_id"])
    items = {i["cheque_id"]: i["cheque"] for i in done["items"] if i["cheque_id"]}
    assert items[a]["state"] == "IN_PORTFOLIO"
    assert items[a]["recovered_by_name"]
    assert items[a]["recovered_at"] is not None
    assert_cheque_accounts_invariant(r.admin, db)
    # Back in the portfolio: it can be deposited or delivered again.
    assert deposit(r.admin, [a], on=today()).status_code == 201
    assert pay(r.operator, perez, [by_cheque(b, "60000")]).status_code == 201


def test_the_recovered_cheques_must_be_of_the_payment(r, box, perez) -> None:  # type: ignore[no-untyped-def]
    a, other = entered(r.admin, "100000"), entered(r.admin, "5000")
    with_cheque = paid(r.operator, perez, [by_cheque(a, "100000")])
    response = reverse_payment(r.admin, with_cheque["id"], recovered_cheque_ids=[a, other])
    assert error(response) == (422, "invalid_recovered_cheques")
    cash_only = paid(r.operator, perez, [cash(box, "1000")])
    assert error(reverse_payment(r.admin, cash_only["id"], recovered_cheque_ids=[a])) == (
        422,
        "invalid_recovered_cheques",
    )
    # A payment without cheques asks for nothing.
    assert reverse_payment(r.admin, cash_only["id"]).status_code == 201


def test_only_an_admin_reverses_and_a_viewer_reads(r, perez) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    assert pay(r.viewer, perez, [by_cheque(a, "100000")]).status_code == 403
    payment = paid(r.operator, perez, [by_cheque(a, "100000")])
    assert reverse_payment(r.operator, payment["id"], recovered_cheque_ids=[a]).status_code == 403
    read = r.viewer.get(f"{BASE}/payments/{payment['id']}").json()
    assert read["items"][0]["cheque"]["cheque_number"] == cheque(r.viewer, a)["cheque_number"]
    assert cheque(r.viewer, a)["state"] == "DELIVERED"


# --- idempotency --------------------------------------------------------------------------------------------


def test_the_same_payment_request_delivers_once(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    body = {
        "client_request_id": str(uuid4()),
        "business_date": today().isoformat(),
        "third_party_id": perez,
        "concept": "MERCHANDISE",
        "items": [by_cheque(a, "100000")],
    }
    first = r.operator.post(f"{BASE}/payments", json=body)
    again = r.operator.post(f"{BASE}/payments", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert len(events(db, a, "DELIVERED")) == 1
    other = entered(r.admin, "100000")
    changed = {**body, "items": [by_cheque(other, "100000")]}
    assert error(r.operator.post(f"{BASE}/payments", json=changed)) == (409, "idempotency_conflict")


def test_the_same_reversal_request_recovers_once(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "100000"), entered(r.admin, "60000")
    payment = paid(r.operator, perez, [by_cheque(a, "100000"), by_cheque(b, "60000")])
    body = {"client_request_id": str(uuid4()), "reason": "Pago duplicado", "recovered_cheque_ids": [a, b]}
    first = r.admin.post(f"{BASE}/payments/{payment['id']}/reverse", json=body)
    again = r.admin.post(f"{BASE}/payments/{payment['id']}/reverse", json={**body, "recovered_cheque_ids": [b, a]})
    assert (first.status_code, again.status_code) == (201, 200)
    assert len(events(db, a, "RECOVERED_FROM_PAYMENT")) == 1
    different = {**body, "recovered_cheque_ids": [a]}
    assert error(r.admin.post(f"{BASE}/payments/{payment['id']}/reverse", json=different)) == (
        409,
        "idempotency_conflict",
    )
    assert error(reverse_payment(r.admin, payment["id"], recovered_cheque_ids=[a, b])) == (
        409,
        "payment_already_reversed",
    )


# --- the rest of the module sees it -------------------------------------------------------------------------


def test_a_settlement_cannot_withdraw_a_delivered_cheque_and_cajas_cannot_reverse_it(r, perez) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    payment = paid(r.operator, perez, [by_cheque(a, "100000")])
    response = r.admin.post(
        f"{BASE}/operations/{payment['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "probando"},
    )
    assert error(response) == (409, "operation_owned_by_document")


# --- expired for presentation (Enzo's adjustment 1 to R6-2) -----------------------------------------------


def _expired_refusal(response: Any, cid: int, deadline: Any) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "cheque_presentation_expired"
    assert response.json()["details"] == {"cheque_id": cid, "deadline": deadline.isoformat()}


def test_1_a_common_cheque_within_its_term_is_delivered(r, perez) -> None:  # type: ignore[no-untyped-def]
    issued = today() - timedelta(days=30)  # its last presentation day is today: it still goes
    a = entered(r.admin, "70000", on=today() - timedelta(days=20), issue_date=issued.isoformat())
    paid(r.operator, perez, [by_cheque(a, "70000")])
    assert cheque(r.admin, a)["state"] == "DELIVERED"


def test_2_and_6_an_expired_common_cheque_is_not_delivered_not_even_by_an_admin(r, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    issued = today() - timedelta(days=40)
    a = entered(r.admin, "70000", on=today() - timedelta(days=35), issue_date=issued.isoformat())
    before = counts(db)
    _expired_refusal(pay(r.operator, perez, [by_cheque(a, "70000")]), a, issued + timedelta(days=30))
    _expired_refusal(pay(r.admin, perez, [by_cheque(a, "70000")]), a, issued + timedelta(days=30))
    assert counts(db) == before
    assert (cheque(r.admin, a)["state"], cheque(r.admin, a)["presentation_expired"]) == ("IN_PORTFOLIO", True)


def test_3_a_deferred_cheque_not_yet_payable_is_delivered(r, perez) -> None:  # type: ignore[no-untyped-def]
    payable = today() + timedelta(days=20)
    a = entered(
        r.admin,
        "80000",
        on=today() - timedelta(days=2),
        issue_date=(today() - timedelta(days=5)).isoformat(),
        payment_type="DEFERRED",
        payment_date=payable.isoformat(),
    )
    assert cheque(r.admin, a)["payable_from"] == payable.isoformat()
    paid(r.operator, perez, [by_cheque(a, "80000")])
    assert cheque(r.admin, a)["state"] == "DELIVERED"


def test_4_a_deferred_cheque_within_its_term_is_delivered(r, perez) -> None:  # type: ignore[no-untyped-def]
    a = entered(
        r.admin,
        "80000",
        on=today() - timedelta(days=10),
        issue_date=(today() - timedelta(days=40)).isoformat(),
        payment_type="DEFERRED",
        payment_date=(today() - timedelta(days=15)).isoformat(),
    )
    paid(r.operator, perez, [by_cheque(a, "80000")])
    assert cheque(r.admin, a)["state"] == "DELIVERED"


def test_5_an_expired_deferred_cheque_is_not_delivered(r, perez) -> None:  # type: ignore[no-untyped-def]
    payable = today() - timedelta(days=40)
    a = entered(
        r.admin,
        "80000",
        on=today() - timedelta(days=35),
        issue_date=(today() - timedelta(days=50)).isoformat(),
        payment_type="DEFERRED",
        payment_date=payable.isoformat(),
    )
    _expired_refusal(pay(r.admin, perez, [by_cheque(a, "80000")]), a, payable + timedelta(days=30))


def test_10_the_term_is_judged_on_the_payment_date_not_today(r, perez) -> None:  # type: ignore[no-untyped-def]
    """A cheque whose last presentation day was yesterday: a payment dated the day before yesterday, recorded
    today, delivers it; one dated today does not. `created_at` plays no part."""
    issued = today() - timedelta(days=31)
    deadline = issued + timedelta(days=30)
    a = entered(r.admin, "60000", on=today() - timedelta(days=20), issue_date=issued.isoformat())
    _expired_refusal(pay(r.operator, perez, [by_cheque(a, "60000")]), a, deadline)
    _expired_refusal(pay(r.operator, perez, [by_cheque(a, "60000")], on=deadline + timedelta(days=1)), a, deadline)
    payment = paid(r.operator, perez, [by_cheque(a, "60000")], on=deadline)
    assert payment["business_date"] == deadline.isoformat()
    assert cheque(r.admin, a)["state"] == "DELIVERED"


# --- CREDITED still reached (Enzo's adjustment 2 to R6-2) -------------------------------------------------


def test_a_credited_cheque_stays_credited_with_deliveries_in_the_model(r, perez) -> None:  # type: ignore[no-untyped-def]
    """Portfolio → deposit → crediting = CREDITED, as since R4-2, next to a delivered cheque: DELIVERED does not
    change it, nor lists it as delivered, nor lets it be paid."""
    kept, given = entered(r.admin, "90000"), entered(r.admin, "30000")
    assert cheque(r.admin, kept)["state"] == "IN_PORTFOLIO"
    deposited(r.admin, [kept])
    assert cheque(r.admin, kept)["state"] == "DEPOSITED"
    credited(r.admin, [kept])
    paid(r.operator, perez, [by_cheque(given, "30000")])
    now = cheque(r.admin, kept)
    assert (now["state"], now["state_label"]) == ("CREDITED", "Acreditado")
    assert now["delivery_payment_id"] is None
    assert cheque(r.admin, given)["state"] == "DELIVERED"
    listed = r.admin.get(f"{BASE}/cheques", params={"state": "CREDITED"}).json()["items"]
    assert [c["id"] for c in listed] == [kept]
    delivered = r.admin.get(f"{BASE}/cheques", params={"state": "DELIVERED"}).json()["items"]
    assert [c["id"] for c in delivered] == [given]
    refused = pay(r.operator, perez, [by_cheque(kept, "90000")])
    assert (refused.status_code, refused.json()["code"]) == (409, "cheque_not_in_portfolio")
