"""Cajas API (R1-2): reading, operations per role, idempotency over HTTP and error contract."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD
from tests.modules.settlements.days import today

BASE = "/api/v1/settlements"


@pytest.fixture
def ids(client: TestClient, as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    accounts = as_role("admin").get(f"{BASE}/accounts").json()
    return {a["code"]: a["id"] for a in accounts}


def op(client: TestClient, kind: str, **body: Any) -> Any:
    body.setdefault("client_request_id", str(uuid4()))
    for key, value in list(body.items()):
        if hasattr(value, "isoformat"):
            body[key] = value.isoformat()
    return client.post(f"{BASE}/operations/{kind}", json=body)


def balances(client: TestClient) -> dict[str, str]:
    return {a["code"] or a["name"]: a["balance"] for a in client.get(f"{BASE}/accounts").json()}


@pytest.fixture
def funded(as_role, ids) -> dict[str, int]:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    for code, amount in (("main_cash", "100000"), ("bank", "50000")):
        response = op(admin, "opening-balance", account_id=ids[code], amount=amount)
        assert response.status_code == 201, response.text
    return ids


# --- reading ----------------------------------------------------------------------------------------


def test_viewer_reads_accounts_with_balances_as_decimal_strings(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    viewer = as_role("viewer")
    accounts = viewer.get(f"{BASE}/accounts").json()
    assert [(a["code"], a["name"], a["kind"], a["balance"]) for a in accounts] == [
        ("main_cash", "Caja principal", "CASH_BOX", "100000.00"),
        ("change_cash", "Caja de cambio", "CASH_BOX", "0.00"),
        ("bank", "Banco", "BANK", "50000.00"),
        ("opening_balance", "Saldo inicial", "OPENING_BALANCE", "-150000.00"),
        ("adjustment", "Ajustes", "ADJUSTMENT", "0.00"),
        ("delivery_collections", "Cobranzas de reparto", "DELIVERY_COLLECTIONS", "0.00"),
        ("expense", "Gastos operativos", "EXPENSE", "0.00"),
        ("settlement_difference", "Diferencias de rendición", "SETTLEMENT_DIFFERENCE", "0.00"),
        ("pending_collections", "Pendientes de cobro", "PENDING_COLLECTIONS", "0.00"),
        ("uncollectible", "Incobrables", "UNCOLLECTIBLE", "0.00"),
        ("cheque_portfolio", "Cartera de cheques", "CHEQUE_PORTFOLIO", "0.00"),
        ("cheques_in_transit", "Cheques depositados", "CHEQUES_IN_TRANSIT", "0.00"),
        ("merchandise_payments", "Pagos por mercadería", "MERCHANDISE_PAYMENTS", "0.00"),
        ("third_party_refunds", "Devoluciones a terceros", "THIRD_PARTY_REFUNDS", "0.00"),
        ("other_payments", "Otros pagos", "OTHER_PAYMENTS", "0.00"),
    ]
    one = viewer.get(f"{BASE}/accounts/{funded['main_cash']}").json()
    assert one["balance"] == "100000.00"
    past = viewer.get(
        f"{BASE}/accounts/{funded['main_cash']}", params={"on": (today() - timedelta(days=1)).isoformat()}
    )
    assert past.json()["balance"] == "0.00"


def test_viewer_reads_movements_and_operation_detail(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    transfer = op(
        admin,
        "cash-transfer",
        from_account_id=funded["main_cash"],
        to_account_id=funded["change_cash"],
        amount="2500.50",
        description="Cambio para repartos",
    ).json()

    viewer = as_role("viewer")
    page = viewer.get(f"{BASE}/accounts/{funded['change_cash']}/movements").json()
    assert page["total"] == 1
    movement = page["items"][0]
    assert movement["operation_id"] == transfer["id"]
    assert movement["operation_type"] == "CASH_TRANSFER"
    assert movement["amount"] == "2500.50"
    assert movement["reversed_by_operation_id"] is None
    assert movement["created_by_name"] == "Usuario Test"

    detail = viewer.get(f"{BASE}/operations/{transfer['id']}").json()
    assert detail["description"] == "Cambio para repartos"
    assert detail["created_by_name"] == "Usuario Test"
    assert [(e["account_name"], e["amount"]) for e in detail["entries"]] == [
        ("Caja principal", "-2500.50"),
        ("Caja de cambio", "2500.50"),
    ]


def test_movements_filters_and_pagination(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    for amount in ("1", "2", "3"):
        op(
            admin,
            "cash-transfer",
            from_account_id=funded["main_cash"],
            to_account_id=funded["change_cash"],
            amount=amount,
        )
    url = f"{BASE}/accounts/{funded['change_cash']}/movements"
    page = admin.get(url, params={"limit": 2}).json()
    assert (page["total"], [m["amount"] for m in page["items"]]) == (3, ["3.00", "2.00"])
    assert admin.get(url, params={"date_to": (today() - timedelta(days=1)).isoformat()}).json()["total"] == 0

    bad = admin.get(
        url, params={"date_from": today().isoformat(), "date_to": (today() - timedelta(days=1)).isoformat()}
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_date_range")
    assert admin.get(url, params={"limit": 500}).status_code == 422


def test_not_found(as_role) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    for path, code in (
        ("/accounts/999999", "account_not_found"),
        ("/accounts/999999/movements", "account_not_found"),
        ("/operations/999999", "operation_not_found"),
    ):
        response = admin.get(f"{BASE}{path}")
        assert (response.status_code, response.json()["code"]) == (404, code)


# --- permissions --------------------------------------------------------------------------------------


def test_without_role_in_rendiciones_everything_is_forbidden(client: TestClient, make_user) -> None:  # type: ignore[no-untyped-def]
    make_user(email="outsider@districom.test", role="viewer")
    client.post("/api/v1/auth/login", json={"email": "outsider@districom.test", "password": PASSWORD})
    assert client.get(f"{BASE}/accounts").status_code == 403
    assert op(client, "cash-transfer", from_account_id=1, to_account_id=2, amount="1").status_code == 403


def test_anonymous_gets_401(client: TestClient) -> None:
    assert client.get(f"{BASE}/accounts").status_code == 401


OPERATOR_OPERATIONS = ["cash-transfer", "bank-deposit", "bank-withdrawal"]
ADMIN_OPERATIONS = ["opening-balance", "owner-contribution", "owner-withdrawal", "adjustment"]


def _body(kind: str, ids: dict[str, int], owner: int | None = None) -> dict[str, Any]:
    return {
        "cash-transfer": {"from_account_id": ids["main_cash"], "to_account_id": ids["change_cash"], "amount": "10"},
        "bank-deposit": {"cash_account_id": ids["main_cash"], "bank_account_id": ids["bank"], "amount": "10"},
        "bank-withdrawal": {"bank_account_id": ids["bank"], "cash_account_id": ids["main_cash"], "amount": "10"},
        "opening-balance": {"account_id": ids["change_cash"], "amount": "10"},
        "owner-contribution": {"owner_account_id": owner, "account_id": ids["bank"], "amount": "10"},
        "owner-withdrawal": {"owner_account_id": owner, "account_id": ids["bank"], "amount": "10"},
        "adjustment": {"account_id": ids["main_cash"], "amount": "-10", "description": "Arqueo"},
    }[kind]


def test_roles_per_operation(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    owner = as_role("admin").post(f"{BASE}/accounts", json={"name": "Socio A"}).json()["id"]

    viewer = as_role("viewer")
    for kind in OPERATOR_OPERATIONS + ADMIN_OPERATIONS:
        response = op(viewer, kind, **_body(kind, funded, owner))
        assert (response.status_code, response.json()["code"]) == (403, "forbidden"), kind

    operator = as_role("operator")
    for kind in OPERATOR_OPERATIONS:
        assert op(operator, kind, **_body(kind, funded, owner)).status_code == 201, kind
    for kind in ADMIN_OPERATIONS:
        assert op(operator, kind, **_body(kind, funded, owner)).status_code == 403, kind
    assert operator.post(f"{BASE}/accounts", json={"name": "Socio B"}).status_code == 403
    assert (
        operator.post(
            f"{BASE}/operations/1/reverse", json={"client_request_id": str(uuid4()), "reason": "x"}
        ).status_code
        == 403
    )

    admin = as_role("admin")
    for kind in OPERATOR_OPERATIONS + ADMIN_OPERATIONS:
        assert op(admin, kind, **_body(kind, funded, owner)).status_code == 201, kind


# --- operations --------------------------------------------------------------------------------------


def test_cash_and_bank_operations_move_the_balances(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert (
        op(
            operator,
            "cash-transfer",
            from_account_id=funded["main_cash"],
            to_account_id=funded["change_cash"],
            amount="30000",
        ).status_code
        == 201
    )
    assert (
        op(
            operator,
            "bank-deposit",
            cash_account_id=funded["main_cash"],
            bank_account_id=funded["bank"],
            amount="50000",
        ).status_code
        == 201
    )
    assert (
        op(
            operator,
            "bank-withdrawal",
            bank_account_id=funded["bank"],
            cash_account_id=funded["change_cash"],
            amount="1000.25",
        ).status_code
        == 201
    )
    assert balances(operator) == {
        "main_cash": "20000.00",
        "change_cash": "31000.25",
        "bank": "98999.75",
        "opening_balance": "-150000.00",
        "adjustment": "0.00",
        "delivery_collections": "0.00",
        "expense": "0.00",
        "settlement_difference": "0.00",
        "pending_collections": "0.00",
        "uncollectible": "0.00",
        "cheque_portfolio": "0.00",
        "cheques_in_transit": "0.00",
        "merchandise_payments": "0.00",
        "third_party_refunds": "0.00",
        "other_payments": "0.00",
    }


def test_partner_contribution_and_withdrawal(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    created = admin.post(f"{BASE}/accounts", json={"name": "  Juan   Pérez "})
    assert created.status_code == 201
    partner = created.json()
    assert (partner["name"], partner["kind"], partner["allow_negative"], partner["balance"]) == (
        "Juan Pérez",
        "OWNER_EQUITY",
        True,
        "0.00",
    )
    op(admin, "owner-contribution", owner_account_id=partner["id"], account_id=funded["bank"], amount="70000")
    op(admin, "owner-withdrawal", owner_account_id=partner["id"], account_id=funded["main_cash"], amount="5000")
    assert balances(admin)["Juan Pérez"] == "-65000.00"
    assert balances(admin)["bank"] == "120000.00"

    # The owner account must be OWNER_EQUITY and the other one cash or bank.
    response = op(
        admin, "owner-contribution", owner_account_id=funded["main_cash"], account_id=funded["bank"], amount="1"
    )
    assert (response.status_code, response.json()["code"]) == (422, "invalid_legs")


def test_adjustment_needs_a_reason_and_can_be_either_sign(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    missing = op(admin, "adjustment", account_id=funded["main_cash"], amount="-10")
    assert (missing.status_code, missing.json()["code"]) == (422, "validation_error")
    blank = op(admin, "adjustment", account_id=funded["main_cash"], amount="-10", description="   ")
    assert (blank.status_code, blank.json()["code"]) == (422, "reason_required")
    assert (
        op(admin, "adjustment", account_id=funded["main_cash"], amount="-10", description="Faltante").status_code == 201
    )
    assert (
        op(admin, "adjustment", account_id=funded["main_cash"], amount="25", description="Sobrante").status_code == 201
    )
    assert balances(admin)["main_cash"] == "100015.00"
    assert balances(admin)["adjustment"] == "-15.00"


def test_reversal_over_http(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    transfer = op(
        admin, "cash-transfer", from_account_id=funded["main_cash"], to_account_id=funded["change_cash"], amount="100"
    ).json()
    key = str(uuid4())
    url = f"{BASE}/operations/{transfer['id']}/reverse"
    first = admin.post(url, json={"client_request_id": key, "reason": "Cargado dos veces"})
    assert first.status_code == 201
    reversal = first.json()
    assert (reversal["type"], reversal["reverses_operation_id"]) == ("REVERSAL", transfer["id"])
    assert admin.get(f"{BASE}/operations/{transfer['id']}").json()["reversed_by_operation_id"] == reversal["id"]
    assert balances(admin)["change_cash"] == "0.00"

    retry = admin.post(url, json={"client_request_id": key, "reason": "Cargado dos veces"})
    assert (retry.status_code, retry.json()["id"]) == (200, reversal["id"])
    again = admin.post(url, json={"client_request_id": str(uuid4()), "reason": "Otra vez"})
    assert (again.status_code, again.json()["code"]) == (409, "operation_already_reversed")
    nested = admin.post(
        f"{BASE}/operations/{reversal['id']}/reverse", json={"client_request_id": str(uuid4()), "reason": "x"}
    )
    assert (nested.status_code, nested.json()["code"]) == (409, "cannot_reverse_reversal")
    missing = admin.post(f"{BASE}/operations/999999/reverse", json={"client_request_id": str(uuid4()), "reason": "x"})
    assert (missing.status_code, missing.json()["code"]) == (404, "operation_not_found")


# --- idempotency over HTTP -----------------------------------------------------------------------------


def test_idempotency_over_http(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    body = {
        "from_account_id": funded["main_cash"],
        "to_account_id": funded["change_cash"],
        "amount": "500000.00",
        "client_request_id": str(uuid4()),
        "business_date": today().isoformat(),
    }
    op(as_role("admin"), "opening-balance", account_id=funded["main_cash"], amount="1000000")
    operator = as_role("operator")

    first = op(operator, "cash-transfer", **body)
    assert first.status_code == 201
    same = op(operator, "cash-transfer", **{**body, "amount": 500000})  # same amount, other format
    assert (same.status_code, same.json()) == (200, first.json())

    different = op(operator, "cash-transfer", **{**body, "amount": "700000"})
    assert different.status_code == 409
    assert different.json()["code"] == "idempotency_conflict"
    assert different.json()["details"] == {"operation_id": first.json()["id"]}
    assert set(different.json()) == {"code", "message", "details", "request_id"}

    other_endpoint = op(
        operator,
        "bank-deposit",
        cash_account_id=funded["main_cash"],
        bank_account_id=funded["bank"],
        amount="500000.00",
        client_request_id=body["client_request_id"],
        business_date=today().isoformat(),
    )
    assert other_endpoint.json()["code"] == "idempotency_conflict"
    assert balances(operator)["change_cash"] == "500000.00"


# --- errors ------------------------------------------------------------------------------------------------


def test_insufficient_funds_now_and_historically(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    now = op(
        operator,
        "cash-transfer",
        from_account_id=funded["main_cash"],
        to_account_id=funded["change_cash"],
        amount="100000.01",
    )
    assert (now.status_code, now.json()["code"]) == (409, "insufficient_funds")
    assert now.json()["details"]["account_id"] == funded["main_cash"]

    past = op(
        operator,
        "cash-transfer",
        from_account_id=funded["main_cash"],
        to_account_id=funded["change_cash"],
        amount="1",
        business_date=today() - timedelta(days=1),
    )
    assert (past.status_code, past.json()["code"]) == (409, "insufficient_funds")
    assert past.json()["details"]["business_date"] == (today() - timedelta(days=1)).isoformat()


def test_inactive_account(db: Session, as_role, funded) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    partner = admin.post(f"{BASE}/accounts", json={"name": "Socio inactivo"}).json()["id"]
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :id"), {"id": partner})
    db.commit()
    response = op(admin, "owner-contribution", owner_account_id=partner, account_id=funded["bank"], amount="1")
    assert (response.status_code, response.json()["code"]) == (409, "account_inactive")
    assert "Socio inactivo" not in [a["name"] for a in admin.get(f"{BASE}/accounts").json()]
    assert "Socio inactivo" in [
        a["name"] for a in admin.get(f"{BASE}/accounts", params={"include_inactive": True}).json()
    ]


@pytest.mark.parametrize(
    ("kind", "body", "status", "code"),
    [
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "bank", "amount": "1"},
            422,
            "invalid_legs",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "main_cash", "amount": "1"},
            422,
            "invalid_legs",
        ),
        (
            "bank-deposit",
            {"cash_account_id": "bank", "bank_account_id": "main_cash", "amount": "1"},
            422,
            "invalid_legs",
        ),
        ("opening-balance", {"account_id": "adjustment", "amount": "1"}, 422, "invalid_legs"),
        (
            "cash-transfer",
            {"from_account_id": 999999, "to_account_id": "main_cash", "amount": "1"},
            422,
            "account_not_found",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "change_cash", "amount": "1.005"},
            422,
            "validation_error",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "change_cash", "amount": "-1"},
            422,
            "validation_error",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "change_cash", "amount": "0"},
            422,
            "validation_error",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "change_cash", "amount": "abc"},
            422,
            "validation_error",
        ),
        (
            "cash-transfer",
            {"from_account_id": "main_cash", "to_account_id": "change_cash", "amount": "1", "extra": True},
            422,
            "validation_error",
        ),
        (
            "cash-transfer",
            {
                "from_account_id": "main_cash",
                "to_account_id": "change_cash",
                "amount": "1",
                "business_date": "2999-01-01",
            },
            422,
            "future_business_date",
        ),
        ("adjustment", {"account_id": "main_cash", "amount": "0", "description": "x"}, 422, "invalid_amount"),
    ],
)
def test_invalid_requests(as_role, funded, kind, body, status, code) -> None:  # type: ignore[no-untyped-def]
    resolved = {k: funded[v] if isinstance(v, str) and v in funded else v for k, v in body.items()}
    response = op(as_role("admin"), kind, **resolved)
    assert (response.status_code, response.json()["code"]) == (status, code), response.text


def test_missing_client_request_id_is_rejected(as_role, funded) -> None:  # type: ignore[no-untyped-def]
    response = as_role("admin").post(
        f"{BASE}/operations/cash-transfer",
        json={"from_account_id": funded["main_cash"], "to_account_id": funded["change_cash"], "amount": "1"},
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation_error")


# --- partner accounts -------------------------------------------------------------------------------------


@pytest.mark.parametrize("duplicate", ["Juan Pérez", "juan perez", "  JUAN   PEREZ ", "Juán Pérez"])
def test_partner_account_cannot_be_duplicated(as_role, duplicate: str) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    first = admin.post(f"{BASE}/accounts", json={"name": "Juan Pérez"}).json()
    response = admin.post(f"{BASE}/accounts", json={"name": duplicate})
    assert response.status_code == 409
    assert response.json()["code"] == "account_name_taken"
    assert response.json()["details"] == {"account_id": first["id"], "active": True}


def test_partner_account_cannot_take_a_system_account_name(as_role) -> None:  # type: ignore[no-untyped-def]
    response = as_role("admin").post(f"{BASE}/accounts", json={"name": "caja principal"})
    assert (response.status_code, response.json()["code"]) == (409, "account_name_taken")


def test_partner_account_creation_is_audited(db: Session, as_role) -> None:  # type: ignore[no-untyped-def]
    account = as_role("admin").post(f"{BASE}/accounts", json={"name": "Socia B"}).json()
    payload = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.account.created' AND entity_id = :id"),
        {"id": str(account["id"])},
    ).scalar_one()
    assert payload == {
        "action": "create",
        "before": None,
        "after": {"name": "Socia B", "kind": "OWNER_EQUITY", "allow_negative": True},
    }


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_partner_account_name_is_validated(as_role, name: str) -> None:  # type: ignore[no-untyped-def]
    response = as_role("admin").post(f"{BASE}/accounts", json={"name": name})
    assert response.status_code == 422


def test_a_database_rejection_at_commit_does_not_leak_postgres_details(  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch, as_role, funded
) -> None:
    """If the service's own check were bypassed, the deferred trigger still rejects the overdraft at commit."""
    from app.modules.settlements.ledger import service

    monkeypatch.setattr(service, "_check_funds", lambda *args, **kwargs: None)
    response = op(
        as_role("operator"),
        "cash-transfer",
        from_account_id=funded["main_cash"],
        to_account_id=funded["change_cash"],
        amount="100000.01",
    )
    assert response.status_code == 409
    body = response.json()
    assert (body["code"], body["details"]) == ("invalid_ledger_operation", None)
    assert "negative" not in response.text
    assert "settlements." not in response.text
    assert balances(as_role("viewer"))["main_cash"] == "100000.00"
