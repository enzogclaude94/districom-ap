"""What the portfolio screens read (R4-3): the list of cheques with its filters (state, bank, payable,
expired, expiring soon), where each one is, its deposit and crediting, and what overdrew a bank."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.platform.settings.service import set_setting
from tests.modules.settlements.days import today
from tests.modules.settlements.test_cheque_cycle_api import (
    BASE,
    account_id,
    credited,
    deposited,
    entered,
    rejected,
)


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


def listed(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/cheques", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    return body["items"]  # type: ignore[no-any-return]


def ids(client: TestClient, **params: Any) -> set[int]:
    return {c["id"] for c in listed(client, **params)}


def test_the_list_filters_by_derived_state(admin: TestClient, driver) -> None:  # type: ignore[no-untyped-def]
    in_portfolio, in_transit, credited_one, rejected_one = (entered(admin) for _ in range(4))
    deposited(admin, [in_transit, credited_one, rejected_one])
    credited(admin, [credited_one])
    rejected(admin, rejected_one, today(), responsible=driver("Ana"))
    assert ids(admin, state="IN_PORTFOLIO") == {in_portfolio}
    assert ids(admin, state="DEPOSITED") == {in_transit}
    assert ids(admin, state="CREDITED") == {credited_one}
    assert ids(admin, state="REJECTED") == {rejected_one}
    assert ids(admin, state="RETURNED") == set()


def test_pagination_counts_the_filtered_cheques(admin: TestClient) -> None:
    created = [entered(admin) for _ in range(3)]
    response = admin.get(f"{BASE}/cheques", params={"state": "IN_PORTFOLIO", "limit": 2, "offset": 0}).json()
    assert response["total"] == 3
    assert [c["id"] for c in response["items"]] == sorted(created, reverse=True)[:2]
    rest = admin.get(f"{BASE}/cheques", params={"state": "IN_PORTFOLIO", "limit": 2, "offset": 2}).json()
    assert [c["id"] for c in rest["items"]] == [min(created)]


def test_location_deposit_and_crediting_of_each_cheque(admin: TestClient) -> None:
    held, sent, paid = (entered(admin) for _ in range(3))
    slip = deposited(admin, [sent, paid])
    crediting = credited(admin, [paid])
    rows = {c["id"]: c for c in listed(admin)}
    assert rows[held]["location_label"] == "Cartera de cheques"
    assert rows[held]["deposit_id"] is None
    assert rows[sent]["location_label"] == "Cheques depositados en Banco"
    assert rows[sent]["deposit_id"] == slip["id"]
    assert rows[sent]["deposit_bank_account_id"] == account_id(admin, "bank")
    assert rows[sent]["deposit_business_date"] == slip["business_date"]
    assert rows[sent]["credit_id"] is None
    assert rows[paid]["location_label"] == "Acreditado en Banco"
    assert rows[paid]["credit_id"] == crediting["id"]
    assert rows[paid]["credit_business_date"] == crediting["business_date"]


def test_payable_expired_and_expiring_soon(admin: TestClient) -> None:
    fresh = entered(admin)  # issued T-12: presented until T+18
    expiring = entered(admin, issue_date=(today() - timedelta(days=25)).isoformat())  # until T+5
    expired = entered(admin, on=today() - timedelta(days=35), issue_date=(today() - timedelta(days=40)).isoformat())
    deferred = entered(
        admin,
        payment_type="DEFERRED",
        issue_date=(today() - timedelta(days=12)).isoformat(),
        payment_date=(today() + timedelta(days=20)).isoformat(),
    )
    assert ids(admin, payable=False) == {deferred}
    assert ids(admin, payable=True) == {fresh, expiring, expired}
    assert ids(admin, expired=True) == {expired}
    assert ids(admin, expiring_soon=True) == {expiring}
    rows = {c["id"]: c for c in listed(admin)}
    assert (rows[expiring]["expires_in_days"], rows[expiring]["expiring_soon"]) == (5, True)
    assert (rows[fresh]["expires_in_days"], rows[fresh]["expiring_soon"]) == (18, False)
    assert (rows[expired]["presentation_expired"], rows[expired]["expires_in_days"]) == (True, None)
    assert rows[deferred]["payable_from"] == (today() + timedelta(days=20)).isoformat()


def test_the_expiring_soon_window_is_a_setting(admin: TestClient, db: Session) -> None:
    fresh = entered(admin)  # 18 days left
    assert fresh not in ids(admin, expiring_soon=True)
    set_setting(db, "settlements.cheque_expiring_soon_days", 20, user_id=None)
    db.commit()
    assert fresh in ids(admin, expiring_soon=True)


def test_the_list_filters_by_issuer_bank(admin: TestClient) -> None:
    cid = entered(admin)
    bank_id = listed(admin)[0]["issuer_bank_id"]
    assert cid in ids(admin, issuer_bank_id=bank_id)
    other = admin.get("/api/v1/financial-institutions", params={"limit": 200}).json()
    other_id = next(b["id"] for b in (other["items"] if isinstance(other, dict) else other) if b["id"] != bank_id)
    assert ids(admin, issuer_bank_id=other_id) == set()


def test_an_overdrawn_bank_names_the_rejections_that_caused_it(admin: TestClient, driver) -> None:  # type: ignore[no-untyped-def]
    cid = entered(admin)
    deposited(admin, [cid])
    credited(admin, [cid])
    bank_id = account_id(admin, "bank")
    withdrawal = admin.post(
        f"{BASE}/operations/bank-withdrawal",
        json={
            "client_request_id": "6b0d3c2a-1f2e-4a5b-9c8d-7e6f5a4b3c2d",
            "business_date": (today() - timedelta(days=5)).isoformat(),
            "bank_account_id": bank_id,
            "cash_account_id": account_id(admin, "main_cash"),
            "amount": "100000",
        },
    )
    assert withdrawal.status_code == 201, withdrawal.text
    rejection = rejected(admin, cid, today(), responsible=driver("Ana"))
    bank = admin.get(f"{BASE}/accounts/{bank_id}").json()
    assert bank["overdrawn"] is True
    assert bank["overdraft_causes"] == [
        {
            "rejection_id": rejection["id"],
            "cheque_id": cid,
            "operation_id": rejection["operation_id"],
            "business_date": today().isoformat(),
            "amount": "100000.00",
        }
    ]
    # Before the rejection the bank was not overdrawn: no causes.
    before = admin.get(f"{BASE}/accounts/{bank_id}", params={"on": (today() - timedelta(days=1)).isoformat()}).json()
    assert (before["overdrawn"], before["overdraft_causes"]) == (False, [])
