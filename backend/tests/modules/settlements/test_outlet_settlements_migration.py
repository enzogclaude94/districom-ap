"""Migration 0023 (R7-2) on data: delivery settlements, closes and reopenings survive the round trip down to
0022 and back; once a boca settlement exists, going down is refused instead of destroying it."""

from collections.abc import Callable

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.outlet_support import close as close_outlet
from tests.modules.settlements.outlet_support import create as create_outlet
from tests.modules.settlements.test_settlement_close_api import close, count, create, reopen

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('assert_zero_close', 'check_zero_closing', "
    "'check_current_closing', 'check_outlet_settlement_insert', 'check_pending_not_outlet', "
    "'check_fuel_not_outlet', 'check_outlet_collections_leg', 'check_outlet_close_collections', "
    "'check_reopening_close')"
)


def _columns(db: Session, table: str) -> set[str]:
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'settlements' AND table_name = :t"
        ),
        {"t": table},
    )
    return set(rows.scalars())


def test_deliveries_go_down_to_0022_and_up_unchanged(as_role, driver, ids: dict[str, int], db: Session) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    closed = create(as_role("operator"), pedro, "1000")
    count(as_role("operator"), closed, ("ARS_1000", 1, ids["main_cash"]))
    first = close(as_role("operator"), closed).json()["close_in_effect"]["operation_id"]
    assert reopen(as_role("admin"), closed, 1).status_code == 200
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0022")
    try:
        assert {"outlet_id", "current_closing_id"}.isdisjoint(_columns(db, "settlements"))
        assert {"kind", "snapshot"}.isdisjoint(_columns(db, "settlement_closings"))
        assert "closing_id" not in _columns(db, "settlement_reopenings")
        assert db.execute(text(OBJECTS)).scalar_one() == 0
        assert (
            db.execute(text("SELECT count(*) FROM settlements.accounts WHERE code = 'outlet_collections'")).scalar_one()
            == 0
        )
        row = db.execute(
            text("SELECT status, version, current_close_operation_id FROM settlements.settlements WHERE id = :s"),
            {"s": closed},
        ).one()
        assert tuple(row) == ("OPEN", 2, first)
        db.rollback()
    finally:
        db.close()
        command.upgrade(cfg, "head")
    db.expire_all()
    body = as_role("viewer").get(f"/api/v1/settlements/deliveries/{closed}").json()
    assert (body["status"], body["financial_state"], body["close_in_effect"]["operation_id"]) == (
        "OPEN",
        "BEING_CORRECTED",
        first,
    )
    assert body["close_in_effect"]["kind"] == "FINANCIAL"
    # The backfill linked the reopening to its close: the settlement closes again as before.
    assert close(as_role("operator"), closed, version=2).status_code == 201


def test_downgrade_is_refused_once_a_boca_settlement_exists(
    r: Roles, boca: Callable[[str], int], ids: dict[str, int], db: Session
) -> None:
    sid = create_outlet(r.operator, boca("CENTRO"), "0")
    assert close_outlet(r.operator, sid).status_code == 201
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="downgrade of 0023 refused"):
        command.downgrade(alembic_config(), "0022")
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != "0022"
    assert db.execute(text("SELECT count(*) FROM settlements.settlements WHERE type = 'OUTLET'")).scalar_one() == 1
