"""A delivery settlement opens without its expected amount (Enzo's R9-1 adjustment 1): nothing assumes it exists.

Both flows end in a normal close: open -> Store Sys fills it -> close, and open -> manual -> close. Until it is loaded
the board says «Sin cargar» (`expected_missing`), there is no difference anywhere, and the close is blocked.
"""

from collections.abc import Callable
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.store_sys_support import (
    DELIVERIES,
    as_user,
    close,
    count_cash,
    delivery,
    history,
    integration,
    open_delivery,
    outcome,
    preview,
    record,
    with_code,
)

__all__ = ["as_user"]


def test_1_a_delivery_opens_without_its_expected_amount(r: Roles, driver: Callable[..., int]) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"))
    body = delivery(r.operator, sid)
    assert body["expected_amount"] is None
    assert body["expected_origin"] is None
    assert body["expected_pending"] is None
    assert body["financial_state"] == "NOT_CLOSED"


def test_2_the_board_says_not_loaded_and_shows_no_difference(
    r: Roles, driver: Callable[..., int], ids: dict[str, int]
) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"))
    count_cash(r.operator, sid, ids["main_cash"], 3)
    summary = delivery(r.operator, sid)["summary"]
    assert summary["expected_missing"] is True
    assert summary["justified"] == "30000.00"
    assert summary["difference"] is None
    assert summary["difference_kind"] is None
    item = r.operator.get(DELIVERIES).json()["items"][0]
    assert item["expected_amount"] is None
    assert item["difference"] is None


def test_3_store_sys_fills_it_and_it_closes(
    r: Roles, driver: Callable[..., int], ids: dict[str, int], db: Session, as_user: Callable[..., TestClient]
) -> None:
    pedro = driver("Pedro Gómez")
    with_code(db, pedro, "R-01")
    sid = open_delivery(r.operator, pedro)
    assert outcome(integration(as_user), record("LIQ-1", "30000.00")) == ("APPLIED", None)
    client = r.operator
    count_cash(client, sid, ids["main_cash"], 3)
    body = delivery(client, sid)
    assert body["expected_amount"] == "30000.00"
    assert body["expected_source"] == "STORE_SYS"
    assert body["expected_origin"]["external_reference"] == "LIQ-1"
    assert close(client, sid).status_code == 201
    closed = db.execute(
        text(
            "SELECT expected_amount, expected_source, expected_reference, expected_revision, expected_event_id "
            "FROM settlements.settlement_closings WHERE settlement_id = :s"
        ),
        {"s": sid},
    ).one()
    assert (str(closed[0]), closed[1], closed[2], closed[3]) == ("30000.00", "STORE_SYS", "LIQ-1", 1)
    assert closed[4] == body["expected_event_id"]


def test_4_a_manual_amount_fills_it_and_it_closes(r: Roles, driver: Callable[..., int], ids: dict[str, int]) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"))
    loaded = r.operator.patch(f"{DELIVERIES}/{sid}", json={"expected_amount": "20000.00"})
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["expected_origin"] == {
        "source": "MANUAL",
        "legacy": False,
        "external_reference": None,
        "revision": None,
        "set_at": loaded.json()["expected_origin"]["set_at"],
        "set_by_name": loaded.json()["expected_origin"]["set_by_name"],
    }
    count_cash(r.operator, sid, ids["main_cash"], 2)
    assert close(r.operator, sid).status_code == 201
    events = history(r.operator, sid)["events"]
    assert [(e["source"], e["outcome"], e["expected_amount"], e["previous_amount"]) for e in events] == [
        ("MANUAL", "MANUAL", "20000.00", None)
    ]


def test_5_the_close_is_blocked_without_it(r: Roles, driver: Callable[..., int], ids: dict[str, int]) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"))
    count_cash(r.operator, sid, ids["main_cash"], 1)
    blockers = preview(r.operator, sid)["blockers"]
    assert [b["code"] for b in blockers] == ["expected_amount_required"]
    # Neutral: Store Sys is not required, a manual load closes it too.
    assert blockers[0]["message"] == "Cargá el importe esperado para cerrar la rendición."
    refused = close(r.operator, sid)
    assert (refused.status_code, refused.json()["code"]) == (422, "expected_amount_required")
    assert refused.json()["message"] == "Cargá el importe esperado para cerrar la rendición."
    assert delivery(r.operator, sid)["status"] == "OPEN"


def test_6_7_zero_and_negative_are_refused(r: Roles, driver: Callable[..., int]) -> None:
    pedro = driver("Pedro Gómez")
    for amount in ("0", "0.00", "-1"):
        created = r.operator.post(
            DELIVERIES,
            json={
                "client_request_id": str(uuid4()),
                "business_date": "2026-01-02",
                "employee_id": pedro,
                "expected_amount": amount,
            },
        )
        assert created.status_code == 422, amount
    sid = open_delivery(r.operator, pedro)
    for amount in ("0", "-5"):
        assert r.operator.patch(f"{DELIVERIES}/{sid}", json={"expected_amount": amount}).status_code == 422
    assert delivery(r.operator, sid)["expected_amount"] is None


def test_8_the_preview_invents_no_difference(r: Roles, driver: Callable[..., int], ids: dict[str, int]) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"))
    count_cash(r.operator, sid, ids["main_cash"], 4)
    body = preview(r.operator, sid)
    assert body["difference"] is None
    assert body["difference_kind"] is None
    assert body["amount_to_allocate"] is None
    assert body["summary"]["expected_missing"] is True
    assert body["summary"]["difference"] is None


def test_a_loaded_delivery_expected_is_never_cleared(r: Roles, driver: Callable[..., int]) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"), "1000")
    refused = r.operator.patch(f"{DELIVERIES}/{sid}", json={"expected_amount": None})
    assert (refused.status_code, refused.json()["code"]) == (422, "invalid_amount")


def test_9_several_deliveries_of_the_day_keep_their_own_state(
    r: Roles, driver: Callable[..., int], ids: dict[str, int]
) -> None:
    pedro = driver("Pedro Gómez")
    loaded = open_delivery(r.operator, pedro, "10000")
    missing = open_delivery(r.operator, pedro)
    count_cash(r.operator, loaded, ids["main_cash"], 1)
    count_cash(r.operator, missing, ids["main_cash"], 2)
    items = {i["id"]: i for i in r.operator.get(DELIVERIES).json()["items"]}
    assert (items[loaded]["expected_amount"], items[loaded]["difference"]) == ("10000.00", "0.00")
    assert (items[missing]["expected_amount"], items[missing]["difference"]) == (None, None)
    assert close(r.operator, loaded).status_code == 201
    assert delivery(r.operator, missing)["status"] == "OPEN"
