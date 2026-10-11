"""R6-3: what was paid to a third party (totals in effect, by concept and by means, the cheques delivered to
it) and the full filters of the payments list. Payments made, never a debt: reversed payments stay in the
history and never add."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.direct_support import BASE, account_id, error, fund
from tests.modules.settlements.payment_support import cash, paid, reverse_payment, third_party, transfer
from tests.modules.settlements.test_cheque_cycle_api import cheque, entered
from tests.modules.settlements.test_payment_cheques_api import Roles


@pytest.fixture
def r(as_role) -> Roles:  # type: ignore[no-untyped-def]
    """One browser: each property logs it in again as that user."""
    return Roles(as_role)


@pytest.fixture
def box(r: Roles) -> int:
    fund(r.admin, "main_cash", "500000", days_ago(10))
    return account_id(r.admin, "main_cash")


@pytest.fixture
def bank(r: Roles) -> int:
    fund(r.admin, "bank", "1000000", days_ago(10))
    return account_id(r.admin, "bank")


@pytest.fixture
def perez(r: Roles) -> int:
    return third_party(r.admin)


@pytest.fixture
def gomez(r: Roles) -> int:
    return third_party(r.admin, "Gómez Servicios", "20123456786")


@pytest.fixture
def tolls(r: Roles) -> int:
    return r.admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]  # type: ignore[no-any-return]


def by_cheque(cid: int, amount: str, reference: str | None = None) -> dict[str, Any]:
    return {"method": "CHEQUE", "cheque_id": cid, "amount": amount, "reference": reference}


def summary(client: TestClient, third_party_id: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/payments/third-parties/{third_party_id}/summary")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def delivered(client: TestClient, third_party_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(f"{BASE}/payments/third-parties/{third_party_id}/cheques", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def listed(client: TestClient, **params: Any) -> tuple[list[int], int, str]:
    response = client.get(f"{BASE}/payments", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return [p["id"] for p in body["items"]], body["total"], body["amount_in_effect"]


def by_key(rows: list[dict[str, Any]], key: str) -> dict[str, tuple[int, str]]:
    return {row[key]: (row["count"], row["amount"]) for row in rows}


# --- the third party's totals -----------------------------------------------------------------------------


def test_a_third_party_without_payments_has_zero_totals(r, perez) -> None:  # type: ignore[no-untyped-def]
    totals = summary(r.viewer, perez)
    assert (totals["payments_in_effect"], totals["amount_in_effect"], totals["payments_reversed"]) == (0, "0.00", 0)
    assert by_key(totals["by_concept"], "concept") == {
        "MERCHANDISE": (0, "0.00"),
        "EXPENSE": (0, "0.00"),
        "REFUND": (0, "0.00"),
        "OTHER": (0, "0.00"),
    }
    assert by_key(totals["by_method"], "method") == {
        "CASH": (0, "0.00"),
        "TRANSFER": (0, "0.00"),
        "CHEQUE": (0, "0.00"),
    }
    assert (totals["cheques_delivered"], totals["cheques_delivered_amount"]) == (0, "0.00")
    assert delivered(r.viewer, perez) == {"items": [], "total": 0}


def test_an_unknown_third_party_is_404(r) -> None:  # type: ignore[no-untyped-def]
    viewer = r.viewer
    assert error(viewer.get(f"{BASE}/payments/third-parties/999999/summary")) == (404, "third_party_not_found")
    assert error(viewer.get(f"{BASE}/payments/third-parties/999999/cheques")) == (404, "third_party_not_found")


def test_totals_by_concept_and_by_means_count_only_payments_in_effect(  # type: ignore[no-untyped-def]
    r, box, bank, perez, gomez, tolls
) -> None:
    a = entered(r.admin, "50000")
    mixed = paid(r.operator, perez, [cash(box, "1000"), transfer(bank, "2000"), by_cheque(a, "50000")])
    paid(r.operator, perez, [transfer(bank, "700.50")], concept="EXPENSE", expense_category_id=tolls)
    paid(r.operator, perez, [cash(box, "300")], concept="REFUND")
    paid(r.admin, perez, [cash(box, "10")], concept="OTHER", description="Ajuste de redondeo")
    gone = paid(r.operator, perez, [cash(box, "99999")])
    assert reverse_payment(r.admin, gone["id"]).status_code == 201
    paid(r.operator, gomez, [cash(box, "5000")])  # another third party never adds here

    totals = summary(r.viewer, perez)
    assert (totals["payments_in_effect"], totals["amount_in_effect"], totals["payments_reversed"]) == (
        4,
        "54010.50",
        1,
    )
    assert by_key(totals["by_concept"], "concept") == {
        "MERCHANDISE": (1, "53000.00"),
        "EXPENSE": (1, "700.50"),
        "REFUND": (1, "300.00"),
        "OTHER": (1, "10.00"),
    }
    # The mixed payment is split among its means: never its whole total under each one.
    methods = by_key(totals["by_method"], "method")
    assert methods == {"CASH": (3, "1310.00"), "TRANSFER": (2, "2700.50"), "CHEQUE": (1, "50000.00")}
    assert sum(Decimal(amount) for _, amount in methods.values()) == Decimal(totals["amount_in_effect"])
    assert (totals["cheques_delivered"], totals["cheques_delivered_amount"]) == (1, "50000.00")
    assert mixed["amount"] == "53000.00"


def test_a_reversed_payment_stays_in_the_history_and_stops_adding(r, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(r.operator, perez, [cash(box, "1500")])
    before = summary(r.admin, perez)
    assert (before["payments_in_effect"], before["amount_in_effect"]) == (1, "1500.00")
    assert reverse_payment(r.admin, payment["id"]).status_code == 201
    after = summary(r.admin, perez)
    assert (after["payments_in_effect"], after["amount_in_effect"], after["payments_reversed"]) == (0, "0.00", 1)
    assert by_key(after["by_method"], "method")["CASH"] == (0, "0.00")
    ids, total, amount = listed(r.admin, third_party_id=perez)
    assert (ids, total, amount) == ([payment["id"]], 1, "0.00")


# --- cheques delivered to the third party ---------------------------------------------------------------------


def test_delivered_cheques_with_their_payment_and_state(r, box, perez) -> None:  # type: ignore[no-untyped-def]
    a, b = entered(r.admin, "100000"), entered(r.admin, "60000")
    payment = paid(r.operator, perez, [cash(box, "1000"), by_cheque(a, "100000"), by_cheque(b, "60000")])
    page = delivered(r.viewer, perez)
    assert page["total"] == 2
    rows = {row["cheque_id"]: row for row in page["items"]}
    for cid, amount in ((a, "100000.00"), (b, "60000.00")):
        row, now = rows[cid], cheque(r.admin, cid)
        assert (row["cheque_number"], row["bank_name"], row["amount"]) == (
            now["cheque_number"],
            now["bank_name"],
            amount,
        )
        assert (row["delivered_on"], row["payment_id"], row["payment_state"]) == (
            today().isoformat(),
            payment["id"],
            "IN_EFFECT",
        )
        assert (row["recovered"], row["recovered_at"], row["state"], row["state_label"]) == (
            False,
            None,
            "DELIVERED",
            "Entregado",
        )


def test_a_recovered_cheque_stays_listed_as_recovered_and_is_not_counted(r, box, perez) -> None:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "100000")
    first = paid(r.operator, perez, [by_cheque(a, "100000")])
    assert reverse_payment(r.admin, first["id"], recovered_cheque_ids=[a]).status_code == 201
    totals = summary(r.admin, perez)
    assert (totals["cheques_delivered"], totals["cheques_delivered_amount"]) == (0, "0.00")
    (row,) = delivered(r.admin, perez)["items"]
    assert (row["cheque_id"], row["payment_id"], row["payment_state"], row["recovered"]) == (
        a,
        first["id"],
        "REVERSED",
        True,
    )
    assert row["recovered_at"] is not None
    assert (row["state"], row["state_label"]) == ("IN_PORTFOLIO", "En cartera")

    # Delivered again in a new payment: both rows stay, only the new one counts.
    second = paid(r.operator, perez, [by_cheque(a, "100000")])
    page = delivered(r.admin, perez)
    assert page["total"] == 2
    assert [(r["payment_id"], r["recovered"], r["state"]) for r in page["items"]] == [
        (second["id"], False, "DELIVERED"),
        (first["id"], True, "DELIVERED"),
    ]
    assert summary(r.admin, perez)["cheques_delivered"] == 1


def test_delivered_cheques_are_paginated(r, perez) -> None:  # type: ignore[no-untyped-def]
    ids = [entered(r.admin, "1000") for _ in range(3)]
    paid(r.operator, perez, [by_cheque(cid, "1000") for cid in ids])
    first, second = delivered(r.admin, perez, limit=2, offset=0), delivered(r.admin, perez, limit=2, offset=2)
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)
    assert {r["cheque_id"] for r in first["items"] + second["items"]} == set(ids)


# --- filters of the payments list -------------------------------------------------------------------------------


@pytest.fixture
def mixed_book(r, box, bank, perez, gomez, tolls) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    a = entered(r.admin, "50000")
    book = {
        "a": a,
        "mixed": paid(
            r.operator, perez, [cash(box, "1000"), transfer(bank, "2000", "TRF-7781"), by_cheque(a, "50000")]
        ),
        "cash": paid(r.operator, perez, [cash(box, "300")], reference="Factura A-0001-99", on=days_ago(3)),
        "toll": paid(r.operator, gomez, [transfer(bank, "700")], concept="EXPENSE", expense_category_id=tolls),
        "gone": paid(r.operator, gomez, [cash(box, "400")], on=days_ago(5)),
    }
    assert reverse_payment(r.admin, book["gone"]["id"]).status_code == 201
    return book


def test_the_method_filter_keeps_a_mixed_payment_whole(r, mixed_book) -> None:  # type: ignore[no-untyped-def]
    mixed, cash_only, toll, gone = (mixed_book[k]["id"] for k in ("mixed", "cash", "toll", "gone"))
    assert listed(r.admin, method="CASH") == ([mixed, cash_only, gone], 3, "53300.00")
    assert listed(r.admin, method="TRANSFER") == ([toll, mixed], 2, "53700.00")
    assert listed(r.admin, method="CHEQUE") == ([mixed], 1, "53000.00")


def test_filters_by_cheque_concept_category_dates_state_account_and_reference(  # type: ignore[no-untyped-def]
    r, bank, perez, gomez, tolls, mixed_book
) -> None:
    mixed, cash_only, toll, gone = (mixed_book[k]["id"] for k in ("mixed", "cash", "toll", "gone"))
    number = cheque(r.admin, mixed_book["a"])["cheque_number"]
    assert listed(r.admin, cheque_id=mixed_book["a"])[0] == [mixed]
    assert listed(r.admin, cheque_number=f"  {number} ")[0] == [mixed]
    assert listed(r.admin, cheque_number="000")[0] == []
    assert listed(r.admin, concept="EXPENSE")[0] == [toll]
    assert listed(r.admin, expense_category_id=tolls)[0] == [toll]
    assert listed(r.admin, third_party_id=gomez) == ([toll, gone], 2, "700.00")
    assert listed(r.admin, date_from=days_ago(4).isoformat(), date_to=days_ago(1).isoformat())[0] == [cash_only]
    assert listed(r.admin, state="REVERSED") == ([gone], 1, "0.00")
    assert listed(r.admin, state="IN_EFFECT")[1] == 3
    assert listed(r.admin, account_id=bank)[0] == [toll, mixed]
    # The reference of the payment or of one of its items, in part and ignoring case; LIKE signs are literal.
    assert listed(r.admin, reference="a-0001")[0] == [cash_only]
    assert listed(r.admin, reference="trf-77")[0] == [mixed]
    assert listed(r.admin, reference="%")[0] == []
    assert listed(r.admin, third_party_id=perez, method="CASH", state="IN_EFFECT") == (
        [mixed, cash_only],
        2,
        "53300.00",
    )


def test_the_filtered_list_is_paginated_by_the_backend(r, mixed_book) -> None:  # type: ignore[no-untyped-def]
    first = r.admin.get(f"{BASE}/payments", params={"method": "CASH", "limit": 2, "offset": 0}).json()
    second = r.admin.get(f"{BASE}/payments", params={"method": "CASH", "limit": 2, "offset": 2}).json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)
    # The sum covers every filtered payment in effect, not just the page.
    assert first["amount_in_effect"] == second["amount_in_effect"] == "53300.00"


@pytest.mark.parametrize(
    ("param", "value"),
    [("method", "CARD"), ("state", "OPEN"), ("concept", "SALARY"), ("cheque_number", "9" * 31)],
)
def test_invalid_filters_are_refused(r, param: str, value: str) -> None:  # type: ignore[no-untyped-def]
    assert r.admin.get(f"{BASE}/payments", params={param: value}).status_code == 422
