"""Migration 0017 on existing data (R5-4): the snapshot of each close in effect is backfilled only where it can
be derived reliably (closed settlements); a settlement being corrected with fuel of its close in effect blocks
the migration before it changes anything (fail safe, Enzo's decision). Each test goes down to 0016 and back up.
The numbers in brackets are Enzo's list of migration tests."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.modules.settlements.delivery import expenses
from tests.conftest import TRUNCATED_TABLES, alembic_config
from tests.modules.settlements.fuel_support import BASE, add_fuel, fuel, fuel_category, last_expense, vehicle
from tests.modules.settlements.test_settlement_close_api import close, create, reopen

SNAPSHOT = (
    "SELECT close_operation_id, settlement_id, settlement_version, fuel_load_id, expense_id, vehicle_id, "
    "employee_id, liters, price_per_liter, amount FROM settlements.settlement_closing_fuel_loads "
    "ORDER BY close_operation_id, fuel_load_id"
)
# What each close in effect published, read from the loads themselves: exact for a closed settlement.
IN_EFFECT = """
SELECT s.current_close_operation_id, s.id, s.version, l.id, e.id, l.vehicle_id, l.employee_id, l.liters,
       l.price_per_liter, e.amount
FROM settlements.settlements s JOIN settlements.expenses e ON e.settlement_id = s.id
JOIN settlements.fuel_loads l ON l.expense_id = e.id
WHERE s.status = 'CLOSED' AND e.voided_at IS NULL
ORDER BY s.current_close_operation_id, l.id
"""


def _rows(db: Session, sql: str) -> list[tuple[object, ...]]:
    db.expire_all()
    return [tuple(r) for r in db.execute(text(sql)).all()]


def _revision(db: Session) -> str | None:
    with db.get_bind().connect() as conn:  # type: ignore[union-attr]
        return MigrationContext.configure(conn).get_current_revision()


def _down(db: Session) -> None:
    db.commit()
    db.close()
    command.downgrade(alembic_config(), "0016")


def _up(db: Session) -> None:
    command.upgrade(alembic_config(), "head")
    db.expire_all()


@pytest.fixture(autouse=True)
def back_at_head(db: Session) -> Iterator[None]:
    """Whatever a test leaves, the next one finds the schema at head (a blocked upgrade leaves 0016)."""
    yield
    db.rollback()
    if _revision(db) != ScriptDirectory.from_config(alembic_config()).get_current_head():
        with db.get_bind().begin() as conn:  # type: ignore[union-attr]
            # Tables of later migrations do not exist yet below head.
            present = [t for t in TRUNCATED_TABLES if conn.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar()]
            conn.execute(text(f"TRUNCATE {', '.join(present)} RESTART IDENTITY CASCADE"))
        command.upgrade(alembic_config(), "head")


@pytest.fixture
def setup(as_role, driver, db: Session) -> dict[str, int]:  # type: ignore[no-untyped-def]
    as_role("admin")
    return {"pedro": driver("Pedro Gómez"), "truck": vehicle(db, "AB123CD"), "fuel": fuel_category(db)}


def loaded(client: TestClient, setup: dict[str, int], *loads: tuple[str, str]) -> int:
    """A settlement whose expected amount is exactly its fuel: it closes balanced without counting."""
    total = sum(round(float(liters) * float(price), 2) for liters, price in loads)
    sid = create(client, setup["pedro"], f"{total:.2f}")
    for liters, price in loads:
        last_expense(add_fuel(client, sid, setup["fuel"], fuel(setup["truck"], liters, price)))
    return sid


def closed(client: TestClient, sid: int, version: int = 1) -> None:
    response = close(client, sid, version=version)
    assert response.status_code == 201, response.text


def test_a_closed_settlement_gets_its_snapshot(as_role, setup, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[1] [7]"""
    operator = as_role("operator")
    sid = loaded(operator, setup, ("40", "1500"))
    closed(operator, sid)
    before = _rows(db, SNAPSHOT)
    _down(db)
    _up(db)
    after = _rows(db, SNAPSHOT)
    assert after == before == _rows(db, IN_EFFECT)
    assert [(str(r[7]), str(r[8]), str(r[9])) for r in after] == [("40.000", "1500.0000", "60000.00")]


def test_several_closed_settlements_get_the_close_in_effect(as_role, setup, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[2] [7] One of them closed twice: only its close in effect is rebuilt, with the corrected values."""
    operator, admin = as_role("operator"), as_role("admin")
    first = loaded(operator, setup, ("10", "2000"), ("5", "2000"))
    second = loaded(operator, setup, ("40", "1500"))
    closed(operator, first)
    closed(operator, second)
    assert reopen(admin, second, 1).status_code == 200
    eid = operator.get(f"{BASE}/deliveries/{second}").json()["expenses"][0]["id"]
    patched = operator.patch(
        f"{BASE}/deliveries/{second}/expenses/{eid}", json={"fuel": {"liters": "37.5", "price_per_liter": "1600"}}
    )
    assert patched.status_code == 200, patched.text
    closed(operator, second, version=2)
    _down(db)
    _up(db)
    rows = _rows(db, SNAPSHOT)
    assert rows == _rows(db, IN_EFFECT)
    assert sorted((r[1], r[2], str(r[7])) for r in rows) == [
        (first, 1, "10.000"),
        (first, 1, "5.000"),
        (second, 2, "37.500"),
    ]


def test_a_settlement_never_closed_gets_no_snapshot(as_role, setup, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[3] Nor does a settlement being corrected whose close published no fuel (its load came later)."""
    operator, admin = as_role("operator"), as_role("admin")
    loaded(operator, setup, ("10", "2000"))
    corrected = create(operator, setup["pedro"], "20000")
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    toll = operator.post(
        f"{BASE}/deliveries/{corrected}/expenses",
        json={"client_request_id": "00000000-0000-4000-8000-000000000017", "category_id": tolls, "amount": "20000"},
    )
    assert toll.status_code == 201, toll.text
    closed(operator, corrected)
    assert reopen(admin, corrected, 1).status_code == 200
    add_fuel(operator, corrected, setup["fuel"], fuel(setup["truck"], "1", "1000"))
    _down(db)
    _up(db)
    assert _rows(db, SNAPSHOT) == []


@pytest.fixture
def correcting(as_role, setup, db: Session) -> int:  # type: ignore[no-untyped-def]
    """Enzo's example: the close published 40 l x $1.500; reopened, the operator proposes 50 l x $1.600."""
    operator = as_role("operator")
    sid = loaded(operator, setup, ("40", "1500"))
    closed(operator, sid)
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    eid = operator.get(f"{BASE}/deliveries/{sid}").json()["expenses"][0]["id"]
    patched = operator.patch(
        f"{BASE}/deliveries/{sid}/expenses/{eid}", json={"fuel": {"liters": "50", "price_per_liter": "1600"}}
    )
    assert patched.status_code == 200, patched.text
    return sid


def test_a_settlement_correcting_its_fuel_blocks_the_migration(correcting, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[4] [5] Refused with the message for the operator, before changing anything: still at 0016, no table,
    the settlement and its load as they were."""
    load = "SELECT liters, price_per_liter FROM settlements.fuel_loads"
    _down(db)
    with pytest.raises(RuntimeError) as refused:
        _up(db)
    assert str(refused.value) == (
        "No se puede migrar R5-4 mientras existan rendiciones con combustible en corrección. "
        f"Finalice el nuevo cierre antes de actualizar. Rendiciones: {correcting}."
    )
    assert _revision(db) == "0016"
    assert "settlement_closing_fuel_loads" not in inspect(db.get_bind()).get_table_names(schema="settlements")
    assert [(str(a), str(b)) for a, b in _rows(db, load)] == [("50.000", "1600.0000")]
    assert _rows(db, f"SELECT status, version FROM settlements.settlements WHERE id = {correcting}") == [("OPEN", 2)]  # noqa: S608


def test_a_voided_fuel_load_in_correction_also_blocks(as_role, setup, db: Session) -> None:  # type: ignore[no-untyped-def]
    """The close published it; voiding it during the correction is still a proposal."""
    operator = as_role("operator")
    sid = loaded(operator, setup, ("10", "2000"))
    closed(operator, sid)
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    eid = operator.get(f"{BASE}/deliveries/{sid}").json()["expenses"][0]["id"]
    voided = operator.post(f"{BASE}/deliveries/{sid}/expenses/{eid}/void", json={"reason": "Era de otro reparto"})
    assert voided.status_code == 200, voided.text
    _down(db)
    with pytest.raises(RuntimeError, match="combustible en corrección"):
        _up(db)
    assert _revision(db) == "0016"


def test_once_the_correction_is_closed_the_upgrade_passes(  # type: ignore[no-untyped-def]
    as_role, setup, correcting, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[6] [7] [8] The correction is finished with the code deployed before R5-4 (whose close writes no fuel
    snapshot), then 0017 runs: the snapshot is the new close's, 50 l x $1.600, never a mix. Down and up again
    stays green."""
    _down(db)
    with pytest.raises(RuntimeError):
        _up(db)
    monkeypatch.setattr(expenses, "publish_fuel", lambda *_: None)
    surplus = {"target": "EMPLOYEE", "employee_id": setup["pedro"], "amount": "20000"}  # $80.000 against $60.000
    response = close(as_role("operator"), correcting, surplus, version=2)
    assert response.status_code == 201, response.text
    monkeypatch.undo()
    _up(db)
    rows = _rows(db, SNAPSHOT)
    assert rows == _rows(db, IN_EFFECT)
    assert [(r[2], str(r[7]), str(r[8]), str(r[9])) for r in rows] == [(2, "50.000", "1600.0000", "80000.00")]
    _down(db)
    _up(db)
    assert _rows(db, SNAPSHOT) == rows
    page = as_role("viewer").get(f"{BASE}/fuel-loads").json()
    assert (page["items"][0]["state"], page["summary"]["liters"]) == ("PUBLISHED", "50.000")
