"""Requests and responses of the delivery settlements API (R2-1). Amounts travel as decimal strings."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.cash.schemas import Amount, Money, Reason

Text500 = Annotated[str, Field(max_length=500)]


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- settlements ------------------------------------------------------------------------------------


class SettlementIn(StrictIn):
    # Generated once per user action and resent on retries: a retry never creates "Rendición 2".
    client_request_id: UUID
    business_date: date
    employee_id: int
    expected_amount: Amount
    external_reference: str | None = Field(default=None, max_length=100)
    notes: Text500 | None = None


class SettlementPatch(StrictIn):
    """Fields of an OPEN settlement that can be corrected. Driver, date and number never change."""

    expected_amount: Amount | None = None
    external_reference: str | None = Field(default=None, max_length=100)
    notes: Text500 | None = None


class Driver(BaseModel):
    id: int
    full_name: str


class CashByBox(BaseModel):
    account_id: int
    account_name: str
    counted: Money
    additions: Money
    total: Money


class Summary(BaseModel):
    """The fixed board of a settlement, computed by the backend only."""

    expected: Money
    cash_counted: Money
    cash_additions: Money
    cash_total: Money
    cash_by_box: list[CashByBox]
    transfers_verified: Money
    transfers_declared: Money
    transfers_rejected: Money
    declared_transfers_count: int
    # Cheques (R4) and pending collections (R3) are always zero until their phase.
    cheques: Money
    pending: Money
    expenses: Money
    justified: Money
    # justified - expected: negative is a shortage, positive a surplus.
    difference: Money
    difference_kind: Literal["SHORTAGE", "SURPLUS", "BALANCED"]


class SettlementItem(BaseModel):
    id: int
    type: str
    business_date: date
    employee_id: int
    employee_name: str
    sequence_number: int
    expected_amount: Money
    external_reference: str | None
    status: str
    version: int
    created_at: datetime
    justified: Money
    difference: Money


class SettlementPage(BaseModel):
    items: list[SettlementItem]
    total: int


class CountLineIn(StrictIn):
    denomination_code: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=0, le=1_000_000)
    destination_account_id: int


class CountIn(StrictIn):
    """The whole count. Lines with quantity 0 are dropped; the previous count is replaced and audited."""

    lines: list[CountLineIn] = Field(max_length=100)


class CountLineOut(BaseModel):
    denomination_code: str
    denomination_value: Money
    quantity: int
    amount: Money
    destination_account_id: int
    destination_account_name: str


class AdditionIn(StrictIn):
    client_request_id: UUID
    amount: Amount
    destination_account_id: int
    description: Text500 | None = None


class VoidIn(StrictIn):
    reason: Reason


class AdditionOut(BaseModel):
    id: int
    amount: Money
    destination_account_id: int
    destination_account_name: str
    description: str | None
    created_by: int
    created_by_name: str
    created_at: datetime
    voided: bool
    voided_by: int | None
    voided_by_name: str | None
    voided_at: datetime | None
    void_reason: str | None


class TransferIn(StrictIn):
    client_request_id: UUID
    amount: Amount
    # Bank account where it should arrive; Banco when omitted.
    bank_account_id: int | None = None
    reference: str | None = Field(default=None, max_length=100)
    payer_name: str | None = Field(default=None, max_length=200)


class TransferPatch(StrictIn):
    """Data of a DECLARED transfer. Once verified or rejected it no longer changes."""

    amount: Amount | None = None
    bank_account_id: int | None = None
    reference: str | None = Field(default=None, max_length=100)
    payer_name: str | None = Field(default=None, max_length=200)


class TransferStatusIn(StrictIn):
    status: Literal["DECLARED", "VERIFIED", "REJECTED"]
    # Mandatory to reject and for every correction of a decision already taken.
    reason: Annotated[str, Field(max_length=500)] | None = None


class TransferEventOut(BaseModel):
    from_status: str | None
    to_status: str
    reason: str | None
    is_correction: bool
    changed_by: int
    changed_by_name: str
    changed_at: datetime


class TransferOut(BaseModel):
    id: int
    amount: Money
    bank_account_id: int
    bank_account_name: str
    reference: str | None
    payer_name: str | None
    status: str
    status_reason: str | None
    status_changed_by: int
    status_changed_by_name: str
    status_changed_at: datetime
    created_by: int
    created_at: datetime
    events: list[TransferEventOut]


class ExpenseIn(StrictIn):
    client_request_id: UUID
    category_id: int
    amount: Amount
    description: Text500 | None = None


class ExpensePatch(StrictIn):
    category_id: int | None = None
    amount: Amount | None = None
    description: Text500 | None = None


class ExpenseOut(BaseModel):
    id: int
    category_id: int
    category_name: str
    amount: Money
    description: str | None
    created_by: int
    created_by_name: str
    created_at: datetime
    voided: bool
    voided_by: int | None
    voided_by_name: str | None
    voided_at: datetime | None
    void_reason: str | None


class SettlementOut(BaseModel):
    id: int
    type: str
    business_date: date
    employee_id: int
    employee_name: str
    sequence_number: int
    expected_amount: Money
    expected_source: str
    external_reference: str | None
    notes: str | None
    status: str
    version: int
    created_by: int
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    # Other settlements of the same driver and day, so the screen can warn before creating another.
    same_day_settlement_ids: list[int]
    count_lines: list[CountLineOut]
    additions: list[AdditionOut]
    transfers: list[TransferOut]
    expenses: list[ExpenseOut]
    summary: Summary


# --- configuration ----------------------------------------------------------------------------------


class ExpenseCategoryIn(StrictIn):
    name: str = Field(min_length=1, max_length=100)


class ExpenseCategoryPatch(StrictIn):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None


class ExpenseCategoryOut(BaseModel):
    id: int
    name: str
    active: bool


class DenominationIn(StrictIn):
    code: str = Field(min_length=1, max_length=40)
    value: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    active: bool = True
    # Code of a cash box (`main_cash`, `change_cash`) or null for no suggestion.
    suggested_destination: str | None = None


class DenominationsIn(StrictIn):
    denominations: list[DenominationIn] = Field(min_length=1, max_length=50)


class DenominationOut(BaseModel):
    code: str
    value: Money
    active: bool
    suggested_destination: str | None
    suggested_destination_account_id: int | None
    suggested_destination_name: str | None
