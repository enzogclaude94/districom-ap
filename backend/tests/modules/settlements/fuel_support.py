"""Shared helpers of the R5-2 fuel tests: the FUEL category, vehicles and fuel expenses."""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

BASE = "/api/v1/settlements"


def fuel_category(db: Session) -> int:
    return int(db.execute(text("SELECT id FROM settlements.expense_categories WHERE code = 'FUEL'")).scalar_one())


def vehicle(db: Session, plate: str, *, active: bool = True) -> int:
    """A vehicle of the platform master, registered by any existing user (the master is tested in R5-1)."""
    vid = db.execute(
        text(
            "INSERT INTO vehicles (plate, plate_key, description, active, created_by, updated_by) "
            "SELECT :p, :p, :d, :a, id, id FROM users ORDER BY id LIMIT 1 RETURNING id"
        ),
        {"p": plate, "d": f"Camión {plate}", "a": active},
    ).scalar_one()
    db.commit()
    return int(vid)


def fuel(vehicle_id: int, liters: str, price: str, **extra: Any) -> dict[str, Any]:
    return {"vehicle_id": vehicle_id, "liters": liters, "price_per_liter": price, **extra}


def add_fuel(client: TestClient, sid: int, category: int, detail: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": category, "fuel": detail, **extra},
    )


def last_expense(response: Any) -> dict[str, Any]:
    return response.json()["expenses"][-1]  # type: ignore[no-any-return]


def make_legacy(db: Session, expense_id: int) -> None:
    """Leave an expense as migration 0015 leaves a fuel expense loaded before R5-2: FUEL, without load,
    legacy. After the migration nothing else can (trigger), so the test lifts the guard for one statement,
    inside one transaction no other session sees, exactly as the migration does."""
    db.execute(text("ALTER TABLE settlements.expenses DISABLE TRIGGER trg_expenses_legacy"))
    db.execute(text("ALTER TABLE settlements.expenses DISABLE TRIGGER trg_expenses_fuel"))
    db.execute(text("ALTER TABLE settlements.fuel_loads DISABLE TRIGGER trg_fuel_loads_expense"))
    db.execute(
        text(
            "UPDATE settlements.expenses SET legacy_fuel_without_detail = true, "
            "category_id = (SELECT id FROM settlements.expense_categories WHERE code = 'FUEL') WHERE id = :e"
        ),
        {"e": expense_id},
    )
    db.execute(text("ALTER TABLE settlements.fuel_loads ENABLE TRIGGER trg_fuel_loads_expense"))
    db.execute(text("ALTER TABLE settlements.expenses ENABLE TRIGGER trg_expenses_fuel"))
    db.execute(text("ALTER TABLE settlements.expenses ENABLE TRIGGER trg_expenses_legacy"))
    db.commit()
