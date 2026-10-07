"""Cheques in a settlement, their portfolio and the administrative entries (R4-1).

The numbers in brackets are the tests Enzo asked for R4-1. The database rules are in
`test_cheques_db_invariants.py`, concurrency in `test_cheques_concurrency.py` and the date rules, identity
strategy and derived states, unit-tested, in `tests/unit/test_cheque_rules.py`.

Every scenario that closes checks the invariant [31]: the balance of "Cartera de cheques" is the sum of
the cheques the ledger has in it.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today

BASE = "/api/v1/settlements"
DAY = business_today() - timedelta(days=1)
ISSUED = DAY - timedelta(days=5)


# --- helpers --------------------------------------------------------------------------------------


def bank(client: TestClient, bcra_code: int = 7) -> int:
    response = client.get("/api/v1/financial-institutions")
    assert response.status_code == 200, response.text
    return next(b["id"] for b in response.json() if b["bcra_code"] == bcra_code)  # type: ignore[no-any-return]


def create(client: TestClient, employee_id: int, expected: str) -> int:
    response = client.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": DAY.isoformat(),
            "employee_id": employee_id,
            "expected_amount": expected,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def physical(bank_id: int, **fields: Any) -> dict[str, Any]:
    return {
        "client_request_id": str(uuid4()),
        "kind": "PHYSICAL",
        "payment_type": "COMMON",
        "amount": "50000",
        "issuer_bank_id": bank_id,
        "branch_code": "123",
        "account_ref": "4567890",
        "cheque_number": "00012345",
        "issue_date": ISSUED.isoformat(),
        "drawer_name": "Distribuidora Sur SA",
        "delivered_by_name": "Almacén Don José",
        "physical_receipt_confirmed": True,
        **fields,
    }


def echeq(bank_id: int, **fields: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "kind": "ECHEQ",
        "physical_receipt_confirmed": False,
        "branch_code": None,
        "account_ref": "0070123401000012345678",
        "drawer_tax_id": "30712345678",
        "echeq_external_id": "ECH-0001",
    }
    return physical(bank_id, **{**defaults, **fields})


def add(client: TestClient, sid: int, body: dict[str, Any]) -> Any:
    return client.post(f"{BASE}/deliveries/{sid}/cheques", json=body)


def new(client: TestClient, sid: int, body: dict[str, Any]) -> int:
    response = add(client, sid, body)
    assert response.status_code == 201, response.text
    return max(c["id"] for c in response.json()["cheques"])  # type: ignore[no-any-return]


def cheque(client: TestClient, cid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/cheques/{cid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def verify(client: TestClient, sid: int, cid: int, status: str = "VERIFIED", reason: str | None = None) -> Any:
    return client.post(f"{BASE}/deliveries/{sid}/cheques/{cid}/verification", json={"status": status, "reason": reason})


def preview(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/deliveries/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def close(client: TestClient, sid: int, version: int = 1, allocations: list[Any] | None = None) -> Any:
    token = preview(client, sid)["preview_token"]
    return client.post(
        f"{BASE}/deliveries/{sid}/close",
        json={"version": version, "preview_token": token, "allocations": allocations or [], "confirm": True},
    )


def closed(client: TestClient, sid: int, version: int = 1) -> dict[str, Any]:
    response = close(client, sid, version)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reopen(client: TestClient, sid: int, version: int) -> None:
    response = client.post(f"{BASE}/deliveries/{sid}/reopen", json={"version": version, "reason": "Corregir cheques"})
    assert response.status_code == 200, response.text


def void(client: TestClient, sid: int, cid: int, reason: str = "Cargado por error") -> Any:
    return client.post(f"{BASE}/deliveries/{sid}/cheques/{cid}/void", json={"reason": reason})


def portfolio_balance(db: Session) -> Decimal:
    db.rollback()
    return db.execute(  # type: ignore[no-any-return]
        text(
            "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.kind = 'CHEQUE_PORTFOLIO'"
        )
    ).scalar_one()


def assert_portfolio_invariant(client: TestClient, db: Session) -> None:
    """[31] "Cartera de cheques" = the sum of the cheques in it, cheque by cheque; and never negative [32]."""
    items = client.get(f"{BASE}/cheques", params={"in_portfolio": True, "limit": 200}).json()["items"]
    in_portfolio = sum((Decimal(c["amount"]) for c in items), Decimal(0))
    assert all(c["state"] == "IN_PORTFOLIO" for c in items)
    balance = portfolio_balance(db)
    assert balance == in_portfolio
    assert balance >= 0
    summary = client.get(f"{BASE}/cheques/summary").json()
    assert Decimal(summary["portfolio_amount"]) == balance
    assert summary["portfolio_count"] == len(items)


@pytest.fixture
def operator(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("operator")  # type: ignore[no-any-return]


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


# --- receipt and verification -----------------------------------------------------------------------


def test_confirmed_physical_cheque_justifies_and_enters_the_portfolio_at_close(  # [1] [23] [29] [30]
    operator: TestClient, pedro: int, db: Session
) -> None:
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    body = operator.get(f"{BASE}/deliveries/{sid}").json()
    assert body["summary"]["cheques"] == "50000.00"
    assert body["summary"]["difference_kind"] == "BALANCED"
    received = cheque(operator, cid)
    assert received["state"] == "RECEIVED"
    assert received["published"] is False
    assert received["physical_receipt_confirmed_by_name"] == "Usuario Test"
    assert portfolio_balance(db) == 0  # [23] nothing until the close

    legs = preview(operator, sid)["legs"]
    cheque_legs = [leg for leg in legs if leg["cheque_id"] is not None]
    assert cheque_legs == [
        {
            "account_id": cheque_legs[0]["account_id"],
            "account_name": "Cartera de cheques",
            "account_kind": "CHEQUE_PORTFOLIO",
            "employee_id": None,
            "cheque_id": cid,
            "amount": "50000.00",
        }
    ]
    closed(operator, sid)
    after = cheque(operator, cid)
    assert after["state"] == "IN_PORTFOLIO"
    assert after["published"] is True
    assert after["in_effect"] is True
    assert [e["event_type"] for e in after["events"]] == ["RECEIVED", "PUBLISHED"]
    assert after["closings"] == [
        {"close_operation_id": after["events"][-1]["close_operation_id"], "settlement_version": 1, "amount": "50000.00"}
    ]
    # [29] balanced close, [30] each portfolio leg names its cheque.
    rows = db.execute(
        text(
            "SELECT a.kind, e.cheque_id, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :op ORDER BY e.id"
        ),
        {"op": after["events"][-1]["close_operation_id"]},
    ).all()
    assert sum(amount for _, _, amount in rows) == 0
    assert [(kind, cheque_id) for kind, cheque_id, _ in rows if cheque_id is not None] == [("CHEQUE_PORTFOLIO", cid)]
    assert all(cheque_id is None for kind, cheque_id, _ in rows if kind != "CHEQUE_PORTFOLIO")
    assert_portfolio_invariant(operator, db)


def test_physical_cheque_without_confirmation_is_not_received(operator: TestClient, pedro: int) -> None:  # [2]
    sid = create(operator, pedro, "50000")
    response = add(operator, sid, physical(bank(operator), physical_receipt_confirmed=False))
    assert response.status_code == 422
    assert response.json()["code"] == "physical_receipt_required"
    assert operator.get(f"{BASE}/deliveries/{sid}").json()["cheques"] == []


def test_declared_echeq_does_not_justify_and_blocks_the_close(operator: TestClient, pedro: int) -> None:  # [3] [4]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    body = operator.get(f"{BASE}/deliveries/{sid}").json()
    assert body["summary"]["cheques"] == "0.00"
    assert body["summary"]["unverified_echeqs_count"] == 1
    assert body["summary"]["difference"] == "-50000.00"
    assert cheque(operator, cid)["state"] == "DECLARED"
    assert [b["code"] for b in preview(operator, sid)["blockers"]] == ["settlement_has_unverified_echeqs"]
    response = close(operator, sid, allocations=[{"target": "EMPLOYEE", "employee_id": pedro, "amount": "50000"}])
    assert response.status_code == 409
    assert response.json()["code"] == "settlement_has_unverified_echeqs"


def test_verified_echeq_justifies(operator: TestClient, pedro: int, db: Session) -> None:  # [5]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    assert verify(operator, sid, cid).status_code == 200
    detail = cheque(operator, cid)
    assert detail["state"] == "RECEIVED"
    assert detail["verification_status"] == "VERIFIED"
    assert operator.get(f"{BASE}/deliveries/{sid}").json()["summary"]["cheques"] == "50000.00"
    closed(operator, sid)
    assert cheque(operator, cid)["state"] == "IN_PORTFOLIO"
    assert_portfolio_invariant(operator, db)


def test_rejected_verification_is_its_own_state(operator: TestClient, pedro: int) -> None:  # [6]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    assert verify(operator, sid, cid, "VERIFICATION_REJECTED").json()["code"] == "reason_required"
    assert verify(operator, sid, cid, "VERIFICATION_REJECTED", "No figura en el home banking").status_code == 200
    detail = cheque(operator, cid)
    assert (detail["state"], detail["state_label"]) == ("VERIFICATION_REJECTED", "Verificación rechazada")
    assert detail["voided"] is False
    body = operator.get(f"{BASE}/deliveries/{sid}").json()
    assert body["summary"]["cheques"] == "0.00"
    assert body["summary"]["unverified_echeqs_count"] == 0  # decided: it no longer blocks the close
    # A rejected ECHEQ no longer holds its identity: the right one can be loaded.
    assert add(operator, sid, echeq(bank(operator))).status_code == 201


def test_correcting_the_verification_is_admin_with_reason_and_keeps_history(  # [7]
    operator: TestClient, as_role, pedro: int
) -> None:  # type: ignore[no-untyped-def]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    verify(operator, sid, cid, "VERIFICATION_REJECTED", "No figura")
    response = verify(operator, sid, cid, "VERIFIED", "Ahora figura")
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    admin = as_role("admin")
    assert verify(admin, sid, cid, "VERIFIED").json()["code"] == "reason_required"
    assert verify(admin, sid, cid, "VERIFIED", "El banco lo acreditó en el listado").status_code == 200
    events = cheque(admin, cid)["events"]
    assert [e["event_type"] for e in events] == ["RECEIVED", "VERIFICATION_REJECTED", "VERIFIED"]
    assert events[1]["reason"] == "No figura"
    assert events[2]["after"] == {"verification_status": "VERIFIED", "correction": True}
    assert events[2]["reason"] == "El banco lo acreditó en el listado"


# --- payment type and dates ---------------------------------------------------------------------------


def test_common_and_deferred_are_different_and_deferred_needs_its_date(  # [8] [9] [10]
    operator: TestClient, pedro: int
) -> None:
    sid = create(operator, pedro, "100000")
    bank_id = bank(operator)
    common = cheque(operator, new(operator, sid, physical(bank_id)))
    assert common["payment_type"] == "COMMON"
    assert common["payable_from"] == ISSUED.isoformat()
    assert common["presentation_deadline"] == (ISSUED + timedelta(days=30)).isoformat()

    missing = add(operator, sid, physical(bank_id, cheque_number="2", payment_type="DEFERRED"))
    assert missing.json()["code"] == "payment_date_required"
    pays_on = DAY + timedelta(days=60)
    deferred = cheque(
        operator,
        new(
            operator,
            sid,
            physical(bank_id, cheque_number="2", payment_type="DEFERRED", payment_date=pays_on.isoformat()),
        ),
    )
    assert deferred["payable_from"] == pays_on.isoformat()
    assert deferred["presentation_deadline"] == (pays_on + timedelta(days=30)).isoformat()

    with_date = add(operator, sid, physical(bank_id, cheque_number="3", payment_date=pays_on.isoformat()))
    assert with_date.json()["code"] == "payment_date_not_allowed"
    before_issue = add(
        operator,
        sid,
        physical(
            bank_id, cheque_number="3", payment_type="DEFERRED", payment_date=(ISSUED - timedelta(days=1)).isoformat()
        ),
    )
    assert before_issue.json()["code"] == "invalid_cheque_date"
    future_issue = add(operator, sid, physical(bank_id, cheque_number="3", issue_date=business_today().isoformat()))
    assert future_issue.json()["code"] == "invalid_cheque_date"
    expired = add(
        operator, sid, physical(bank_id, cheque_number="3", issue_date=(DAY - timedelta(days=31)).isoformat())
    )
    assert expired.json()["code"] == "cheque_presentation_expired"


# --- identity -------------------------------------------------------------------------------------------


def test_complete_physical_identity_blocks_a_duplicate(operator: TestClient, pedro: int, driver) -> None:  # type: ignore[no-untyped-def]  # [11]
    sid = create(operator, pedro, "50000")
    bank_id = bank(operator)
    first = new(operator, sid, physical(bank_id))
    other = create(operator, driver("Ana"), "50000")
    # Same instrument typed differently (zeros, dots) in another settlement.
    response = add(
        operator, other, physical(bank_id, cheque_number="12.345", account_ref="0004567890", branch_code="0123")
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cheque_duplicate"
    assert response.json()["details"]["cheque_id"] == first
    # Not even confirming it as "another" one.
    confirmed = add(
        operator,
        other,
        physical(bank_id, cheque_number="12345", confirm_not_duplicate=True, duplicate_reason="Otro"),
    )
    assert confirmed.json()["code"] == "cheque_duplicate"
    # Another account with the same number is another instrument.
    assert add(operator, other, physical(bank_id, account_ref="999")).status_code == 201


def test_incomplete_identity_is_a_possible_duplicate_confirmed_with_reason(  # [13] [14]
    operator: TestClient, pedro: int
) -> None:
    sid = create(operator, pedro, "100000")
    bank_id = bank(operator)
    first = new(operator, sid, physical(bank_id, branch_code=None, account_ref=None))
    response = add(operator, sid, physical(bank_id, branch_code=None, account_ref=None, amount="30000"))
    assert response.status_code == 409
    assert response.json()["code"] == "cheque_possible_duplicate"
    assert [c["cheque_id"] for c in response.json()["details"]["candidates"]] == [first]
    no_reason = add(operator, sid, physical(bank_id, branch_code=None, account_ref=None, confirm_not_duplicate=True))
    assert no_reason.json()["code"] == "reason_required"
    second = new(
        operator,
        sid,
        physical(
            bank_id,
            branch_code=None,
            account_ref=None,
            amount="30000",
            confirm_not_duplicate=True,
            duplicate_reason="Otro librador, otra chequera",
        ),
    )
    received = cheque(operator, second)["events"][0]
    assert received["event_type"] == "RECEIVED"
    assert received["after"]["duplicate_confirmation"] == {
        "candidates": [first],
        "reason": "Otro librador, otra chequera",
    }


def test_echeq_external_identity_is_unique(operator: TestClient, pedro: int, driver) -> None:  # type: ignore[no-untyped-def]  # [15]
    sid = create(operator, pedro, "50000")
    bank_id = bank(operator)
    new(operator, sid, echeq(bank_id, echeq_external_id=" ech-0001 "))
    other = create(operator, driver("Ana"), "50000")
    response = add(operator, other, echeq(bank_id, cheque_number="999", account_ref="0070999901000099999999"))
    assert (response.status_code, response.json()["code"]) == (409, "cheque_duplicate")


def test_declared_echeq_without_identity_exists_but_is_not_verified(  # [16]
    operator: TestClient, pedro: int
) -> None:
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator), echeq_external_id=None, account_ref=None))
    detail = cheque(operator, cid)
    assert (detail["state"], detail["identity_strategy"], detail["identity_complete"]) == ("DECLARED", None, False)
    response = verify(operator, sid, cid)
    assert (response.status_code, response.json()["code"]) == (422, "echeq_identity_required")
    assert close(operator, sid).json()["code"] == "settlement_has_unverified_echeqs"
    # With its bank data (CBU + CUIT, no electronic id) it can be verified and published.
    patched = operator.patch(f"{BASE}/deliveries/{sid}/cheques/{cid}", json={"account_ref": "0070123401000012345678"})
    assert patched.status_code == 200, patched.text
    assert cheque(operator, cid)["identity_strategy"] == "ECHEQ_BANK_DATA"
    assert verify(operator, sid, cid).status_code == 200
    closed(operator, sid)
    assert cheque(operator, cid)["state"] == "IN_PORTFOLIO"


# --- bank catalog ---------------------------------------------------------------------------------------


def test_cheque_names_its_bank_by_catalog_fk(operator: TestClient, as_role, pedro: int, db: Session) -> None:  # type: ignore[no-untyped-def]  # [17]
    sid = create(operator, pedro, "50000")
    detail = cheque(operator, new(operator, sid, physical(bank(operator, 7))))
    assert detail["bank_code"] == "007"
    assert detail["bank_name"].startswith("Banco de Galicia")
    assert add(operator, sid, physical(999999)).json()["code"] == "invalid_bank"
    admin = as_role("admin")
    galicia = bank(admin, 7)
    assert admin.patch(f"/api/v1/financial-institutions/{galicia}", json={"active": False}).status_code == 200
    inactive = add(admin, sid, physical(galicia, cheque_number="77"))
    assert inactive.json()["code"] == "invalid_bank"
    # The cheque already loaded keeps naming its bank.
    assert cheque(admin, detail["id"])["bank_code"] == "007"


# --- corrections before publication, immutability after -----------------------------------------------


def test_corrections_before_publication(operator: TestClient, pedro: int) -> None:  # [19]
    sid = create(operator, pedro, "60000")
    cid = new(operator, sid, physical(bank(operator)))
    response = operator.patch(
        f"{BASE}/deliveries/{sid}/cheques/{cid}",
        json={"amount": "60000", "cheque_number": "12346", "delivered_by_name": "Kiosco Ana"},
    )
    assert response.status_code == 200, response.text
    detail = cheque(operator, cid)
    assert (detail["amount"], detail["cheque_number"], detail["delivered_by_name"]) == (
        "60000.00",
        "12346",
        "Kiosco Ana",
    )
    corrected = detail["events"][-1]
    assert corrected["event_type"] == "CORRECTED"
    assert corrected["before"] == {
        "amount": "50000.00",
        "cheque_number": "00012345",
        "delivered_by_name": "Almacén Don José",
    }


def test_identity_amount_and_delivered_by_are_locked_after_publication(  # [20] [22]
    operator: TestClient, as_role, pedro: int
) -> None:  # type: ignore[no-untyped-def]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    admin = as_role("admin")
    reopen(admin, sid, 1)
    for change in (
        {"amount": "40000"},
        {"cheque_number": "1"},
        {"issuer_bank_id": bank(admin, 11)},
        {"account_ref": "1"},
        {"payment_type": "DEFERRED", "payment_date": (DAY + timedelta(days=5)).isoformat()},
        {"delivered_by_name": "Otro cliente"},
    ):
        response = admin.patch(f"{BASE}/deliveries/{sid}/cheques/{cid}", json=change)
        assert (response.status_code, response.json()["code"]) == (409, "cheque_published_immutable"), change
    assert cheque(admin, cid)["amount"] == "50000.00"


def test_descriptive_data_is_admin_with_reason(operator: TestClient, as_role, pedro: int) -> None:  # type: ignore[no-untyped-def]  # [21]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    body = {
        "drawer_name": "Distribuidora del Sur S.A.",
        "notes": "Firmado por el socio",
        "reason": "Razón social completa",
    }
    assert operator.post(f"{BASE}/cheques/{cid}/descriptive", json=body).status_code == 403
    admin = as_role("admin")
    assert (
        admin.post(f"{BASE}/cheques/{cid}/descriptive", json={**body, "reason": " "}).json()["code"]
        == "reason_required"
    )
    response = admin.post(f"{BASE}/cheques/{cid}/descriptive", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["drawer_name"] == "Distribuidora del Sur S.A."
    event = response.json()["events"][-1]
    assert (event["event_type"], event["reason"]) == ("DESCRIPTIVE_CORRECTED", "Razón social completa")
    assert event["before"] == {"drawer_name": "Distribuidora Sur SA", "notes": None}


# --- corrections of a closed settlement ---------------------------------------------------------------


def test_cheque_added_in_a_correction_enters_only_with_the_new_close(  # [25] [24]
    operator: TestClient, as_role, pedro: int, db: Session
) -> None:  # type: ignore[no-untyped-def]
    sid = create(operator, pedro, "50000")
    first = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    admin = as_role("admin")
    reopen(admin, sid, 1)
    second = new(admin, sid, physical(bank(admin), cheque_number="777", amount="20000"))
    added = cheque(admin, second)
    assert (added["state"], added["load_change"], added["in_effect"]) == ("RECEIVED", "ADDED", False)
    assert portfolio_balance(db) == Decimal("50000")
    response = close(admin, sid, 2, allocations=[{"target": "EMPLOYEE", "employee_id": pedro, "amount": "20000"}])
    assert response.status_code == 201, response.text
    assert cheque(admin, second)["state"] == "IN_PORTFOLIO"
    # [24] the snapshot of each close is exactly what it put in the portfolio.
    snapshots = db.execute(
        text(
            "SELECT settlement_version, cheque_id, amount FROM settlements.settlement_closing_cheques "
            "ORDER BY settlement_version, cheque_id"
        )
    ).all()
    assert [tuple(r) for r in snapshots] == [
        (1, first, Decimal("50000.00")),
        (2, first, Decimal("50000.00")),
        (2, second, Decimal("20000.00")),
    ]
    assert_portfolio_invariant(admin, db)


def test_cheque_marked_for_removal_stays_until_the_close_and_then_leaves(  # [26] [27]
    operator: TestClient, as_role, pedro: int, db: Session
) -> None:  # type: ignore[no-untyped-def]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    admin = as_role("admin")
    reopen(admin, sid, 1)
    assert void(admin, sid, cid).status_code == 200
    marked = cheque(admin, cid)
    assert (marked["state"], marked["load_change"], marked["voided"]) == ("IN_PORTFOLIO", "MARKED_FOR_REMOVAL", True)
    assert portfolio_balance(db) == Decimal("50000")
    add_cash = admin.post(
        f"{BASE}/deliveries/{sid}/additions",
        json={"client_request_id": str(uuid4()), "amount": "50000", "destination_account_id": _main_cash(db)},
    )
    assert add_cash.status_code == 201, add_cash.text
    closed(admin, sid, 2)
    removed = cheque(admin, cid)
    assert (removed["state"], removed["in_effect"]) == ("VOIDED", False)
    assert [e["event_type"] for e in removed["events"]][-1] == "WITHDRAWN"
    assert portfolio_balance(db) == 0
    assert_portfolio_invariant(admin, db)


def test_preview_token_changes_on_material_changes(operator: TestClient, pedro: int) -> None:  # [28]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    tokens = [preview(operator, sid)["preview_token"]]
    operator.patch(f"{BASE}/deliveries/{sid}/cheques/{cid}", json={"delivered_by_name": "Kiosco Ana"})
    tokens.append(preview(operator, sid)["preview_token"])
    operator.patch(
        f"{BASE}/deliveries/{sid}/cheques/{cid}",
        json={"payment_type": "DEFERRED", "payment_date": (DAY + timedelta(days=9)).isoformat()},
    )
    tokens.append(preview(operator, sid)["preview_token"])
    verify(operator, sid, cid)
    tokens.append(preview(operator, sid)["preview_token"])
    assert len(set(tokens)) == len(tokens)
    # A close confirming a stale preview is refused.
    stale = operator.post(
        f"{BASE}/deliveries/{sid}/close",
        json={"version": 1, "preview_token": tokens[0], "allocations": [], "confirm": True},
    )
    assert stale.json()["code"] == "settlement_preview_stale"


def test_cheque_accounts_are_not_available_money(as_role, db: Session) -> None:  # type: ignore[no-untyped-def]  # [33]
    admin = as_role("admin")
    accounts = {a["kind"]: a for a in admin.get(f"{BASE}/accounts").json()}
    for kind, name in (("CHEQUE_PORTFOLIO", "Cartera de cheques"), ("CHEQUES_IN_TRANSIT", "Cheques depositados")):
        assert accounts[kind]["name"] == name
        assert accounts[kind]["allow_negative"] is False
    # Cajas never moves money into or out of them, as if a cheque were cash in a box or in the bank.
    for target in ("CHEQUE_PORTFOLIO", "CHEQUES_IN_TRANSIT"):
        response = admin.post(
            f"{BASE}/operations/cash-transfer",
            json={
                "client_request_id": str(uuid4()),
                "from_account_id": accounts["CASH_BOX"]["id"],
                "to_account_id": accounts[target]["id"],
                "amount": "1",
            },
        )
        assert response.status_code == 422, response.text
    summary = admin.get(f"{BASE}/cheques/summary").json()
    assert summary == {
        "portfolio_count": 0,
        "portfolio_amount": "0.00",
        "in_transit_count": 0,
        "in_transit_amount": "0.00",
    }


# --- administrative entries --------------------------------------------------------------------------


def _entry(bank_id: int, **fields: Any) -> dict[str, Any]:
    body = physical(bank_id, **fields)
    body.pop("physical_receipt_confirmed")
    return {**body, "physical_receipt_confirmed": True, "business_date": DAY.isoformat()}


def test_opening_balance_is_admin_only(operator: TestClient, as_role, db: Session) -> None:  # type: ignore[no-untyped-def]  # [34]
    response = operator.post(f"{BASE}/cheques/opening-balance", json=_entry(bank(operator)))
    assert response.status_code == 403
    admin = as_role("admin")
    created = admin.post(f"{BASE}/cheques/opening-balance", json=_entry(bank(admin)))
    assert created.status_code == 201, created.text
    detail = created.json()
    assert (detail["origin_type"], detail["state"], detail["settlement_id"]) == (
        "OPENING_BALANCE",
        "IN_PORTFOLIO",
        None,
    )
    assert detail["entry_operation_id"] is not None
    assert [e["event_type"] for e in detail["events"]] == ["RECEIVED", "PUBLISHED"]
    legs = db.execute(
        text(
            "SELECT a.kind, e.amount, e.cheque_id FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :op ORDER BY e.amount"
        ),
        {"op": detail["entry_operation_id"]},
    ).all()
    assert [tuple(r) for r in legs] == [
        ("OPENING_BALANCE", Decimal("-50000.00"), None),
        ("CHEQUE_PORTFOLIO", Decimal("50000.00"), detail["id"]),
    ]
    assert_portfolio_invariant(admin, db)
    # The ledger refuses to reverse it from Cajas: it is the cheque's own document.
    reversed_ = admin.post(
        f"{BASE}/operations/{detail['entry_operation_id']}/reverse",
        json={"reason": "x", "client_request_id": str(uuid4())},
    )
    assert reversed_.status_code in (404, 409, 422)


def test_owner_contribution_is_admin_only(operator: TestClient, as_role, owner_account, db: Session) -> None:  # type: ignore[no-untyped-def]  # [35]
    owner = owner_account("Aportes Enzo")
    body = {**_entry(bank(operator), kind="ECHEQ", physical_receipt_confirmed=False), "owner_account_id": owner}
    body.update(
        physical_receipt_confirmed=False,
        branch_code=None,
        account_ref="0070123401000012345678",
        drawer_tax_id="30712345678",
    )
    assert operator.post(f"{BASE}/cheques/owner-contribution", json=body).status_code == 403
    admin = as_role("admin")
    wrong = admin.post(f"{BASE}/cheques/owner-contribution", json={**body, "owner_account_id": _main_cash(db)})
    assert wrong.json()["code"] == "invalid_owner_account"
    created = admin.post(f"{BASE}/cheques/owner-contribution", json=body)
    assert created.status_code == 201, created.text
    detail = created.json()
    assert (detail["origin_type"], detail["state"], detail["verification_status"]) == (
        "OWNER_CONTRIBUTION",
        "IN_PORTFOLIO",
        "VERIFIED",
    )
    assert [e["event_type"] for e in detail["events"]] == ["RECEIVED", "VERIFIED", "PUBLISHED"]
    assert_portfolio_invariant(admin, db)


# --- history, idempotency, conflicts ---------------------------------------------------------------


def test_history_does_not_depend_on_audit_events(operator: TestClient, pedro: int, db: Session) -> None:  # [37]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, echeq(bank(operator)))
    verify(operator, sid, cid)
    closed(operator, sid)
    db.execute(text("TRUNCATE audit_events"))
    db.commit()
    events = cheque(operator, cid)["events"]
    assert [e["event_type"] for e in events] == ["RECEIVED", "VERIFIED", "PUBLISHED"]


def test_creation_is_idempotent(operator: TestClient, pedro: int) -> None:  # [38]
    sid = create(operator, pedro, "50000")
    body = physical(bank(operator))
    first = add(operator, sid, body)
    again = add(operator, sid, body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert len(again.json()["cheques"]) == 1
    other = add(operator, sid, {**body, "amount": "1"})
    assert (other.status_code, other.json()["code"]) == (409, "idempotency_conflict")


def test_conflicts(operator: TestClient, as_role, pedro: int) -> None:  # type: ignore[no-untyped-def]  # [39]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    # A closed settlement's load does not change.
    response = operator.patch(f"{BASE}/deliveries/{sid}/cheques/{cid}", json={"notes": "x"})
    assert (response.status_code, response.json()["code"]) == (409, "settlement_closed")
    assert add(operator, sid, physical(bank(operator), cheque_number="9")).json()["code"] == "settlement_closed"
    # A physical cheque is not verified; a voided one is not voided again.
    admin = as_role("admin")
    reopen(admin, sid, 1)
    assert verify(admin, sid, cid).json()["code"] == "not_an_echeq"
    assert void(admin, sid, cid).status_code == 200
    assert void(admin, sid, cid).json()["code"] == "cheque_already_voided"
    # A cheque of another settlement is not found from this one.
    assert void(admin, sid + 1000, cid).status_code == 404


def _main_cash(db: Session) -> int:
    return db.execute(text("SELECT id FROM settlements.accounts WHERE code = 'main_cash'")).scalar_one()  # type: ignore[no-any-return]


_MOVE = (
    "WITH op AS (INSERT INTO settlements.financial_operations (type, business_date, description, "
    "client_request_id, request_fingerprint, created_by) VALUES ('ADJUSTMENT', :d, 'Movimiento posterior', :k, "
    "repeat('a', 64), (SELECT min(id) FROM users)) RETURNING id) "
    "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount, cheque_id) "
    "SELECT op.id, a.id, CAST(:amount AS numeric), :c FROM op, settlements.accounts a "
    "WHERE a.code = 'cheque_portfolio' "
    "UNION ALL SELECT op.id, a.id, -CAST(:amount AS numeric), NULL FROM op, settlements.accounts a "
    "WHERE a.code = 'adjustment'"
)


def test_only_a_cheque_without_later_activity_leaves_its_settlement(  # [27]
    operator: TestClient, as_role, pedro: int, db: Session
) -> None:  # type: ignore[no-untyped-def]
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    # A later movement of the cheque (R4-2 will have deposits; here, a raw one out and back).
    for amount in ("-50000", "50000"):
        db.execute(text(_MOVE), {"d": DAY, "k": uuid4(), "amount": amount, "c": cid})
    db.commit()
    admin = as_role("admin")
    reopen(admin, sid, 1)
    response = void(admin, sid, cid)
    assert (response.status_code, response.json()["code"]) == (409, "cheque_has_activity")
    assert cheque(admin, cid)["state"] == "IN_PORTFOLIO"
