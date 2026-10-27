"""Migration 0030 (R9-1) on data: R2-R8 survive the round trip down to 0029 and back with their expected amounts
untouched (nothing is backfilled), and any expected amount event (manual or Store Sys), Store Sys amount or delivery
without its expected amount refuses the downgrade: the history of the expected amount is never dropped."""

from collections.abc import Callable, Iterator
from decimal import Decimal

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


EVENTS = (
    "SELECT id, source, outcome, expected_amount FROM settlements.expected_amount_events WHERE settlement_id = :s "
    "ORDER BY id"
)


def _legacy_settlement(db: Session) -> None:
    """A boca loaded before 0030 (`boca("CEN")` and a user must exist): an amount without an event."""
    db.execute(text("ALTER TABLE settlements.settlements DISABLE TRIGGER trg_settlements_expected_event"))
    db.execute(
        text(
            "INSERT INTO settlements.settlements (type, business_date, outlet_id, sequence_number, expected_amount, "
            "client_request_id, request_fingerprint, created_by) SELECT 'OUTLET', current_date - 1, o.id, 1, 50, "
            "gen_random_uuid(), repeat('a', 64), u.id FROM users u, outlets o ORDER BY u.id LIMIT 1"
        )
    )
    db.commit()
    db.execute(text("ALTER TABLE settlements.settlements ENABLE TRIGGER trg_settlements_expected_event"))
    db.commit()


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


def test_a_manual_event_refuses_the_downgrade_and_stays_intact(
    r: Roles, driver: Callable[..., int], db: Session, back_at_head: None
) -> None:
    """`expected_amount_events` is the history of the expected amount: a downgrade never drops it, manual or not."""
    sid = open_delivery(r.operator, driver("Pedro Gómez"), "1000")
    before = db.execute(text(EVENTS), {"s": sid}).all()
    assert [row[1:] for row in before] == [("MANUAL", "MANUAL", Decimal("1000.00"))]
    db.close()
    with pytest.raises(DBAPIError, match="downgrade refused"):
        command.downgrade(alembic_config(), "0029")
    db.close()
    assert db.execute(text(OBJECTS)).scalar_one() == 5
    assert db.execute(text(EVENTS), {"s": sid}).all() == before
    linked = db.execute(text("SELECT expected_event_id FROM settlements.settlements WHERE id = :s"), {"s": sid})
    assert linked.scalar_one() == before[0][0]


def test_legacy_settlements_go_down_and_up_with_their_amount_and_no_event(
    r: Roles, boca: Callable[[str], int], db: Session, back_at_head: None
) -> None:
    """A database without R9 data goes down to 0029 and back."""
    assert r.operator  # a user to author it
    boca("CEN")
    cfg = alembic_config()
    _legacy_settlement(db)  # loaded before 0030: an amount without an event, nothing of R9
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
    assert db.execute(text("SELECT expected_amount FROM settlements.settlements")).scalar_one() == Decimal("50.00")


def test_nothing_is_backfilled_for_settlements_loaded_before(r: Roles, boca: Callable[[str], int], db: Session) -> None:
    """A settlement whose amount was loaded before R9-1 has no event: «Manual · anterior a integración Store Sys»."""
    boca("CEN")
    _legacy_settlement(db)
    sid = db.execute(text("SELECT id FROM settlements.settlements")).scalar_one()
    origin = r.operator.get(f"/api/v1/settlements/settlements/{sid}/expected-history").json()["origin"]
    assert origin["source"] == "MANUAL"
    assert origin["legacy"] is True
