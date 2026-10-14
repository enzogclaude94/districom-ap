"""Migration 0024 (R7-3) on data: boca settlements of R7-2 (closed, open, being corrected) survive the round trip
down to 0023 and back; once a remittance exists, going down is refused instead of destroying it."""

from collections.abc import Callable

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.outlet_support import OUT, close, count, create, reopen
from tests.modules.settlements.remittance_support import remit, reverse

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_outlet_advances_account', 'check_outlet_remittance', "
    "'check_outlet_advances_leg', 'verify_outlet_remittance', 'check_outlet_remittance_operation', "
    "'check_closing_remittance', 'check_close_remittances', 'assert_outlet_advances', "
    "'check_outlet_advances_balance')"
)


def _tables(db: Session) -> set[str]:
    rows = db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'settlements'"))
    return set(rows.scalars())


def test_boca_settlements_go_down_to_0023_and_up_unchanged(
    r: Roles, boca: Callable[[str], int], ids: dict[str, int], db: Session
) -> None:
    centro = boca("CENTRO")
    closed = create(r.operator, centro, "1000")
    count(r.operator, closed, ("ARS_1000", 1, ids["main_cash"]))
    assert close(r.operator, closed).status_code == 201
    corrected = create(r.operator, centro, "2000", confirm_additional=True)
    count(r.operator, corrected, ("ARS_1000", 2, ids["main_cash"]))
    assert close(r.operator, corrected).status_code == 201
    assert reopen(r.admin, corrected, 1).status_code == 200
    still_open = create(r.operator, centro, None, confirm_additional=True)
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0023")
    try:
        assert {"outlet_remittances", "settlement_closing_remittances"}.isdisjoint(_tables(db))
        assert db.execute(text(OBJECTS)).scalar_one() == 0
        columns = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'settlements' AND table_name = 'accounts'"
            )
        ).scalars()
        assert "outlet_id" not in set(columns)
        zero_close = db.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'assert_zero_close'")).scalar_one()
        assert "remittance" not in zero_close
        states = db.execute(
            text("SELECT id, status FROM settlements.settlements WHERE type = 'OUTLET' ORDER BY id")
        ).all()
        assert [tuple(s) for s in states] == [(closed, "CLOSED"), (corrected, "OPEN"), (still_open, "OPEN")]
        db.rollback()
    finally:
        db.close()
        command.upgrade(cfg, "head")
    db.expire_all()
    # Back on 0024 they work as before, and now take remittances.
    rid = remit(r.operator, still_open, "500", ids["main_cash"])
    reverse(r.admin, still_open, rid)
    assert close(r.operator, corrected, version=2).status_code == 201
    body = r.viewer.get(f"{OUT}/{closed}").json()
    assert (body["status"], body["remittances"]) == ("CLOSED", [])


@pytest.mark.parametrize("reversed_", [False, True])
def test_downgrade_is_refused_once_a_remittance_exists(
    r: Roles, boca: Callable[[str], int], ids: dict[str, int], db: Session, reversed_: bool
) -> None:
    """Even reversed, a remittance is history: going down would destroy it and its operations."""
    sid = create(r.operator, boca("CENTRO"), "1000")
    rid = remit(r.operator, sid, "400", ids["main_cash"])
    if reversed_:
        reverse(r.admin, sid, rid)
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="downgrade of 0024 refused"):
        command.downgrade(alembic_config(), "0023")
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != "0023"
    assert db.execute(text("SELECT count(*) FROM settlements.outlet_remittances")).scalar_one() == 1
