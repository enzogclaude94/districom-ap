"""Fuel loads (R5-2): the detail of a FUEL expense and the one rule that turns it into money.

The amount of a fuel expense is round(liters x price per liter, 2), half up, computed here and nowhere else;
it is stored only in `expenses.amount` and the database checks it. A load names an active vehicle of the
platform master and an active employee, by default the settlement's driver, when it is created or when they
change; a load whose vehicle or employee was later deactivated keeps showing them.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery.models import FuelLoad
from app.platform.parties.models import Employee
from app.platform.vehicles import service as vehicles

CENT = Decimal("0.01")
LITER = Decimal("0.001")
PRICE = Decimal("0.0001")
MAX_AMOUNT = Decimal("999999999999.99")  # numeric(14,2)


def amount(liters: Decimal, price_per_liter: Decimal) -> Decimal:
    """Liters x price per liter in cents, half up: 0,125 l x $1,0000 = $0,13."""
    total = (liters * price_per_liter).quantize(CENT, ROUND_HALF_UP)
    if total <= 0:
        raise AppError(
            422,
            "fuel_amount_zero",
            "Litros por precio por litro da menos de un centavo. Revisá los valores.",
            {"liters": str(liters), "price_per_liter": str(price_per_liter)},
        )
    if total > MAX_AMOUNT:
        raise AppError(
            422, "amount_too_large", "El importe calculado supera el máximo permitido.", {"amount": str(total)}
        )
    return total


def require_vehicle(db: Session, vehicle_id: int) -> None:
    vehicles.active(db, vehicle_id)


def require_employee(db: Session, employee_id: int | None) -> int:
    employee = db.get(Employee, employee_id) if employee_id is not None else None
    if employee is None or not employee.active:
        raise AppError(
            422, "invalid_employee", "El empleado que cargó tiene que ser uno activo.", {"employee_id": employee_id}
        )
    return employee.id


def snapshot(load: FuelLoad) -> dict[str, Any]:
    return {
        "vehicle_id": load.vehicle_id,
        "employee_id": load.employee_id,
        "liters": str(load.liters.quantize(LITER)),
        "price_per_liter": str(load.price_per_liter.quantize(PRICE)),
        "odometer_km": load.odometer_km,
    }
