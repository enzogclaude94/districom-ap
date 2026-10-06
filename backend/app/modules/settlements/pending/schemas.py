"""Requests and responses of pending collections (R3-1, R3-2). Amounts travel as decimal strings.

Every response keeps the load (what the next close publishes) apart from the financial amount in effect
(what the close in effect published), so a correction in progress never looks like money already moved.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.cash.schemas import Amount, Money, Reason
from app.modules.settlements.pending.state import LoadChange, State

CustomerName = Annotated[str, Field(min_length=1, max_length=200)]
PendingReason = Annotated[str, Field(min_length=1, max_length=500)]


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PendingIn(StrictIn):
    # Generated once per user action and resent on retries: a retry never creates a second pending.
    client_request_id: UUID
    customer_name: CustomerName
    amount: Amount
    reason: PendingReason
    # The driver of the settlement when omitted (R3-D3).
    responsible_employee_id: int | None = None
    expected_date: date | None = None


class PendingPatch(StrictIn):
    """Fields of the load that can be corrected while the settlement is OPEN. Omitted fields do not change;
    `expected_date: null` clears it."""

    customer_name: CustomerName | None = None
    amount: Amount | None = None
    reason: PendingReason | None = None
    responsible_employee_id: int | None = None
    expected_date: date | None = None


class ReceiptIn(StrictIn):
    """A customer paid (part of) a pending. CASH enters a cash box, TRANSFER a bank."""

    client_request_id: UUID
    amount: Amount
    method: Literal["CASH", "TRANSFER"]
    destination_account_id: int
    # When the customer paid; today when omitted. Never before the settlement nor in the future.
    business_date: date | None = None


class ResolutionPartIn(StrictIn):
    """Who absorbs part of a resolution: the company (no employee) or one employee."""

    target: Literal["COMPANY", "EMPLOYEE"]
    employee_id: int | None = None
    amount: Amount


class ResolutionIn(StrictIn):
    """Administrative resolution of (part of) a pending's balance: admin only, with a reason."""

    client_request_id: UUID
    amount: Amount
    reason: PendingReason
    allocations: list[ResolutionPartIn] = Field(min_length=1, max_length=20)
    business_date: date | None = None


class DocumentReversalIn(StrictIn):
    """Reverse a receipt or a resolution: it was wrong. Admin only, with a reason; idempotent."""

    client_request_id: UUID
    reason: Reason


class ResponsibleIn(StrictIn):
    """Who answers for collecting the pending; an admin may change it after the close."""

    responsible_employee_id: int
    reason: Annotated[str, Field(max_length=500)] | None = None


class PendingOut(BaseModel):
    id: int
    settlement_id: int
    # The load: what the next close of the settlement will publish.
    customer_name: str
    amount: Money
    reason: str
    responsible_employee_id: int
    responsible_name: str
    expected_date: date | None
    created_by: int
    created_by_name: str
    created_at: datetime
    updated_by: int | None
    updated_by_name: str | None
    updated_at: datetime
    voided: bool
    voided_by: int | None
    voided_by_name: str | None
    voided_at: datetime | None
    void_reason: str | None
    # Financial, from the close in effect only: null while it is not in Cajas (DRAFT / VOIDED).
    state: State
    amount_in_effect: Money | None
    customer_in_effect: str | None
    collected: Money
    resolved: Money
    balance: Money | None
    # What a correction in progress proposes for it, not confirmed until the new close.
    load_change: LoadChange | None
    load_change_detail: str | None
    # Hours since it was registered; it alerts when it is in effect with a balance and older than the setting.
    age_hours: int
    alert: bool
    # The expected date passed and it still owes; independent of the alert.
    expected_date_overdue: bool


class PendingListItem(PendingOut):
    settlement_business_date: date
    settlement_sequence_number: int
    driver_id: int
    driver_name: str


class ReversalOut(BaseModel):
    operation_id: int
    reversed_by: int
    reversed_by_name: str
    reversed_at: datetime
    reason: str


class ReceiptOut(BaseModel):
    id: int
    amount: Money
    business_date: date
    method: str
    destination_account_id: int
    destination_name: str
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: ReversalOut | None


class ResolutionPartOut(BaseModel):
    target: str
    employee_id: int | None
    employee_name: str | None
    amount: Money


class ResolutionOut(BaseModel):
    id: int
    amount: Money
    business_date: date
    reason: str
    allocations: list[ResolutionPartOut]
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: ReversalOut | None


class PublishedOut(BaseModel):
    """What one close of the settlement published for the pending."""

    settlement_version: int
    close_operation_id: int
    amount: Money
    customer_name: str
    in_effect: bool
    published_at: datetime


class PendingEventOut(BaseModel):
    id: int
    event_type: str
    settlement_version: int
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    close_operation_id: int | None
    receipt_id: int | None
    resolution_id: int | None
    created_by: int
    created_by_name: str
    created_at: datetime


class PendingDetailOut(PendingOut):
    settlement_business_date: date
    settlement_sequence_number: int
    driver_id: int
    driver_name: str
    settlement_financial_state: str
    published: list[PublishedOut]
    receipts: list[ReceiptOut]
    resolutions: list[ResolutionOut]
    events: list[PendingEventOut]


class PendingPage(BaseModel):
    items: list[PendingListItem]
    total: int


class PendingAlertSummary(BaseModel):
    """Pendings in alert: in effect, with a balance, older than `alert_hours`."""

    alert_hours: int
    count: int
    balance: Money
