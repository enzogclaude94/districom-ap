"""Responses of the fuel history (R5-4): one row per real fuel load, of a settlement or a direct expense,
with its state, and the summary of the fuel in effect for the same filters."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.settlements.cash.schemas import Money

Origin = Literal["SETTLEMENT", "DIRECT"]
# OPEN_SETTLEMENT: loaded in a settlement never closed, no financial effect yet. PUBLISHED: in a closed
# settlement's close in effect, or a direct expense in effect. IN_CORRECTION: its settlement was reopened;
# the close in effect still holds what it published. VOIDED: a settlement expense voided. REVERSED: a
# direct expense whose DIRECT_EXPENSE was reversed.
FuelState = Literal["OPEN_SETTLEMENT", "PUBLISHED", "IN_CORRECTION", "VOIDED", "REVERSED"]


class FuelInEffect(BaseModel):
    """What the close in effect published for a load of a settlement being corrected."""

    vehicle_id: int
    vehicle_plate: str
    employee_id: int
    employee_name: str
    liters: Decimal
    price_per_liter: Decimal
    amount: Money


class FuelLoadRow(BaseModel):
    id: int
    expense_id: int
    origin: Origin
    state: FuelState
    business_date: date
    vehicle_id: int
    vehicle_plate: str
    vehicle_description: str
    vehicle_active: bool
    employee_id: int
    employee_name: str
    employee_active: bool
    liters: Decimal
    price_per_liter: Decimal
    amount: Money
    odometer_km: int | None
    # SETTLEMENT: the settlement, its number of the day, date and driver.
    settlement_id: int | None
    settlement_sequence: int | None
    settlement_driver_name: str | None
    # DIRECT: the cash box or bank it was paid from.
    paid_from_account_name: str | None
    created_by_name: str
    created_at: datetime
    # IN_CORRECTION only: what the close in effect holds (null for a load added during the correction).
    in_effect: FuelInEffect | None
    # IN_CORRECTION only: the load differs from the close in effect, or was voided in the correction.
    proposed_change: bool
    proposed_void: bool


class FuelSummary(BaseModel):
    """The fuel in effect among the filtered loads: never an open settlement's load, a voided or reversed
    one, nor what a correction proposes before its close."""

    liters: Decimal
    amount: Money
    # amount / liters, rounded to the cent; null when no liter is in effect.
    weighted_price_per_liter: Money | None


class FuelLoadPage(BaseModel):
    items: list[FuelLoadRow]
    total: int
    limit: int
    offset: int
    summary: FuelSummary


class FuelEmployee(BaseModel):
    id: int
    full_name: str
    active: bool


class FuelLoadQuery(BaseModel):
    vehicle_id: int | None = None
    employee_id: int | None = None
    # Business date: the settlement's for a settlement load, the expense's for a direct one.
    date_from: date | None = None
    date_to: date | None = None
    origin: Origin | None = None
    state: FuelState | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
