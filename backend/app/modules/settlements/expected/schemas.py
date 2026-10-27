"""Requests and responses of the expected amount: the Store Sys canonical contract (R9-1, ADR 011), the history and
the decision on a pending update. Amounts travel as decimal strings."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.settlements.cash.schemas import Money, Reason

Outcome = Literal[
    "APPLIED", "MATCHED", "PENDING_CONFIRMATION", "CONFIRMED", "KEPT", "REJECTED_CLOSED", "SUPERSEDED", "MANUAL"
]
Source = Literal["MANUAL", "STORE_SYS"]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
MAX_RECORDS = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreSysRecordIn(_Strict):
    """One record of the canonical contract: only the expected amount of one settlement, identified by business data.
    `employee_store_code` and `outlet_code` are DistriCom's canonical fields; the adapter maps Store Sys to them."""

    external_reference: Code
    revision: int = Field(ge=1)
    settlement_type: Literal["DELIVERY", "OUTLET"]
    business_date: date
    employee_store_code: Code | None = None
    outlet_code: Code | None = None
    sequence_number: int | None = Field(default=None, ge=1)
    # A decimal string with at most two decimals (never a float); the type decides its range.
    expected_amount: Decimal = Field(max_digits=14, decimal_places=2)
    reported_at: AwareDatetime | None = None


class StoreSysImportIn(_Strict):
    source: Literal["STORE_SYS"]
    client_request_id: UUID
    # Validated one by one by the service: a bad record is a result of its own, never a reason to drop the batch.
    records: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_RECORDS)


class SettlementRefOut(BaseModel):
    """The settlement a record reached, by business data only."""

    type: Literal["DELIVERY", "OUTLET"]
    business_date: date
    sequence_number: int


class RecordResultOut(BaseModel):
    index: int
    external_reference: str | None
    revision: int | None
    # Null when the record was refused: then `code` and `message` say why and nothing was stored.
    outcome: Outcome | None
    code: str | None = None
    message: str | None = None
    settlement: SettlementRefOut | None = None


class ImportCounts(BaseModel):
    received: int
    stored: int
    refused: int
    by_outcome: dict[str, int]


class StoreSysImportOut(BaseModel):
    results: list[RecordResultOut]
    counts: ImportCounts


class KeepIn(_Strict):
    reason: Reason


class ExpectedEventOut(BaseModel):
    id: int
    source: Source
    outcome: Outcome
    expected_amount: Money | None
    previous_amount: Money | None
    previous_source: Source | None
    external_reference: str | None
    revision: int | None
    reported_at: datetime | None
    settlement_version: int
    reason: str | None
    received_by_name: str | None
    received_at: datetime
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # Whether this event set the amount in effect now.
    in_effect: bool


class ExpectedOriginOut(BaseModel):
    """Where the expected amount in effect comes from, next to it on the settlement."""

    source: Source
    # Loaded before R9-1: no event («Manual · anterior a integración Store Sys»).
    legacy: bool
    external_reference: str | None = None
    revision: int | None = None
    set_at: datetime | None = None
    set_by_name: str | None = None


class ExpectedPendingOut(BaseModel):
    """A Store Sys update waiting for a decision: the expected amount does not change until someone accepts it."""

    event_id: int
    expected_amount: Money
    current_amount: Money | None
    external_reference: str
    revision: int
    received_at: datetime
    # Being corrected: deciding takes an admin of Rendiciones.
    requires_admin: bool


class ExpectedHistoryOut(BaseModel):
    settlement_id: int
    events: list[ExpectedEventOut]
    origin: ExpectedOriginOut | None
    pending: ExpectedPendingOut | None
