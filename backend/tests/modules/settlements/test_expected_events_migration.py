"""Migration 0030 (R9-1) on data: R2-R8 survive the round trip down to 0029 and back with their expected amounts
untouched (nothing is backfilled; manual events go down with their table, their trail stays in `audit_events`), and a
database with Store Sys records or a delivery without its expected amount refuses the downgrade."""

from collections.abc import Callable, Iterator

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.settlements.delivery.models import Settlement
from tests.conftest import alembic_config
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.store_sys_support import as_user, integration, open_delivery, record, sent, with_code

__all__ = ["as_user"]

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_expected_amount_event', "
    "'check_expected_amount_event_superseded', 'check_settlement_expected_change', 'check_settlement_expected_event', "
    "'freeze_closing_expected')"
)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_a_delivery_without_its_expected_amount_refuses_the_downgrade(
    r: Roles, driver: Callable[..., int], db: Session, back_at_head: None
) -> None:
    open_delivery(r.operator, driver("Pedro Gómez"))  # without an expected amount: no event, but R9-1 data
    db.close()
    with pytest.raises(DBAPIError, match="downgrade refused"):
        command.downgrade(alembic_config(), "0029")
    db.close()
    assert db.execute(text(OBJECTS)).scalar_one() == 5


def test_store_sys_records_refuse_the_downgrade(
    r: Roles, driver: Callable[..., int], db: Session, as_user: Callable[..., TestClient], back_at_head: None
) -> None:
    pedro = driver("Pedro Gómez")
    with_code(db, pedro, "R-01")
    open_delivery(r.operator, pedro, "1000")
    sent(integration(as_user), record("LIQ-1", "1500.00"))  # pending: the amount in effect is still manual
    db.close()
    with pytest.raises(DBAPIError, match="downgrade refused"):
        command.downgrade(alembic_config(), "0029")
    db.close()
    assert db.execute(text("SELECT count(*) FROM settlements.expected_amount_events")).scalar_one() == 2


def test_manual_events_go_down_and_the_amount_comes_back_as_before_the_integration(
    r: Roles, driver: Callable[..., int], db: Session, back_at_head: None
) -> None:
    sid = open_delivery(r.operator, driver("Pedro Gómez"), "1000")
    db.close()
    command.downgrade(alembic_config(), "0029")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    db.close()
    command.upgrade(alembic_config(), "head")
    row = db.execute(
        text("SELECT expected_amount, expected_source, expected_event_id FROM settlements.settlements WHERE id = :s"),
        {"s": sid},
    ).one()
    assert (str(row[0]), row[1], row[2]) == ("1000.00", "MANUAL", None)
    assert db.execute(text("SELECT count(*) FROM settlements.expected_amount_events")).scalar_one() == 0
    trail = db.execute(
        text("SELECT count(*) FROM audit_events WHERE event_type = 'settlements.expected.manual'")
    ).scalar_one()
    assert trail >= 1


def test_legacy_settlements_go_down_and_up_with_their_amount_and_no_event(db: Session, back_at_head: None) -> None:
    cfg = alembic_config()
    db.close()
    command.downgrade(cfg, "0029")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    columns = db.execute(
        text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'settlements' "
            "AND column_name = 'expected_event_id'"
        )
    ).scalar_one()
    assert columns == 0
    db.close()
    command.upgrade(cfg, "head")
    assert db.execute(text(OBJECTS)).scalar_one() == 5
    assert db.query(Settlement).filter(Settlement.expected_event_id.is_not(None)).count() == 0


def test_nothing_is_backfilled_for_settlements_loaded_before(r: Roles, boca: Callable[[str], int], db: Session) -> None:
    """A settlement whose amount was loaded before R9-1 has no event: «Manual · anterior a integración Store Sys»."""
    boca("CEN")
    db.execute(text("ALTER TABLE settlements.settlements DISABLE TRIGGER trg_settlements_expected_event"))
    db.execute(
        text(
            "INSERT INTO settlements.settlements (type, business_date, outlet_id, sequence_number, expected_amount, "
            "client_request_id, request_fingerprint, created_by) SELECT 'OUTLET', current_date - 1, o.id, 1, 50, "
            "gen_random_uuid(), repeat('a', 64), u.id FROM users u, outlets o ORDER BY u.id LIMIT 1"
        )
    )
    db.commit()  # as a settlement loaded before 0030 was: an amount without an event
    db.execute(text("ALTER TABLE settlements.settlements ENABLE TRIGGER trg_settlements_expected_event"))
    db.commit()
    sid = db.execute(text("SELECT id FROM settlements.settlements")).scalar_one()
    origin = r.operator.get(f"/api/v1/settlements/settlements/{sid}/expected-history").json()["origin"]
    assert origin["source"] == "MANUAL"
    assert origin["legacy"] is True
