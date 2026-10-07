"""Requests and responses of cheques (R4-1). Amounts travel as decimal strings.

A cheque response always separates what the instrument is (amount, identity, dates, drawer, who
delivered it) from where it is (`state`, derived) and from what a correction in progress proposes
(`load_change`), so a load never looks like a cheque already in the portfolio.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.cash.schemas import Amount, Money, Reason
from app.modules.settlements.cheques.state import LoadChange, State

Name = Annotated[str, Field(min_length=1, max_length=200)]
Code = Annotated[str, Field(min_length=1, max_length=40)]


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DuplicateConfirmation(StrictIn):
    """Answer to `cheque_possible_duplicate`: it is another instrument, and why (kept in its history)."""

    confirm_not_duplicate: bool = False
    duplicate_reason: Annotated[str, Field(max_length=500)] | None = None


class InstrumentIn(DuplicateConfirmation):
    kind: Literal["PHYSICAL", "ECHEQ"]
    payment_type: Literal["COMMON", "DEFERRED"]
    amount: Amount
    issuer_bank_id: int
    branch_code: Code | None = None
    account_ref: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    cheque_number: Code
    echeq_external_id: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    issue_date: date
    # DEFERRED only: from when it can be collected.
    payment_date: date | None = None
    drawer_name: Name
    drawer_tax_id: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    delivered_by_name: Name
    # PHYSICAL only, and required: "Recibí el cheque físicamente" (Enzo's R4-D3).
    physical_receipt_confirmed: bool = False
    notes: Annotated[str, Field(max_length=1000)] | None = None


class ChequeIn(InstrumentIn):
    """A cheque of a settlement's load. Received on the settlement's date, by its driver unless said."""

    # Generated once per user action and resent on retries: a retry never creates a second cheque.
    client_request_id: UUID
    received_by_employee_id: int | None = None


class ChequePatch(DuplicateConfirmation):
    """Correction of a cheque not yet published. Omitted fields do not change; nullable ones clear with null."""

    payment_type: Literal["COMMON", "DEFERRED"] | None = None
    amount: Amount | None = None
    issuer_bank_id: int | None = None
    branch_code: Code | None = None
    account_ref: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    cheque_number: Code | None = None
    echeq_external_id: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    issue_date: date | None = None
    payment_date: date | None = None
    drawer_name: Name | None = None
    drawer_tax_id: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    delivered_by_name: Name | None = None
    received_by_employee_id: int | None = None
    notes: Annotated[str, Field(max_length=1000)] | None = None


class VerificationIn(StrictIn):
    """Verify an ECHEQ or reject its verification (reason required). An admin corrects a decision with a
    reason while the cheque is not published."""

    status: Literal["VERIFIED", "VERIFICATION_REJECTED"]
    reason: Reason | None = None


class DescriptiveIn(StrictIn):
    """Admin, with a reason: data that does not identify the instrument nor who owes it."""

    drawer_name: Name | None = None
    drawer_tax_id: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    notes: Annotated[str, Field(max_length=1000)] | None = None
    reason: Reason


class AdministrativeEntryIn(InstrumentIn):
    """A cheque entering the portfolio outside a settlement (admin): already held when Rendiciones starts,
    or a partner's contribution. An ECHEQ needs a verifiable identity and is verified by this admin."""

    client_request_id: UUID
    # When the company got it; today when omitted. Never in the future.
    business_date: date | None = None
    description: Annotated[str, Field(max_length=500)] | None = None


class OwnerContributionIn(AdministrativeEntryIn):
    owner_account_id: int


class ChequeOut(BaseModel):
    id: int
    kind: str
    payment_type: str
    amount: Money
    issuer_bank_id: int
    bank_bcra_code: int
    bank_name: str
    branch_code: str | None
    account_ref: str | None
    cheque_number: str
    echeq_external_id: str | None
    # Which data identifies it (ECHEQ_EXTERNAL_ID, ECHEQ_BANK_DATA, PHYSICAL_BANK_DATA), null if none yet.
    identity_strategy: str | None
    identity_complete: bool
    issue_date: date
    payment_date: date | None
    payable_from: date
    presentation_deadline: date
    drawer_name: str
    drawer_tax_id: str | None
    delivered_by_name: str
    origin_type: str
    settlement_id: int | None
    received_business_date: date
    received_by_employee_id: int | None
    received_by_name: str | None
    physical_receipt_confirmed_by: int | None
    physical_receipt_confirmed_by_name: str | None
    physical_receipt_confirmed_at: datetime | None
    verification_status: str | None
    verification_changed_by: int | None
    verification_changed_by_name: str | None
    verification_changed_at: datetime | None
    verification_reason: str | None
    notes: str | None
    state: State
    state_label: str
    # Whether it ever had a financial effect: from then on amount and identity never change.
    published: bool
    # Whether the settlement's close in effect has it in the portfolio.
    in_effect: bool
    load_change: LoadChange | None
    load_change_detail: str | None
    created_by: int
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    voided: bool
    voided_by: int | None
    voided_by_name: str | None
    voided_at: datetime | None
    void_reason: str | None


class ChequeEventOut(BaseModel):
    id: int
    event_type: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    settlement_id: int | None
    settlement_version: int | None
    close_operation_id: int | None
    operation_id: int | None
    created_by: int
    created_by_name: str
    created_at: datetime


class ChequeClosingOut(BaseModel):
    close_operation_id: int
    settlement_version: int
    amount: Money


class ChequeDetailOut(ChequeOut):
    events: list[ChequeEventOut]
    # Each close of its settlement that had it in the portfolio.
    closings: list[ChequeClosingOut]
    # The operation that put it in the portfolio outside a settlement, if any.
    entry_operation_id: int | None


class ChequePage(BaseModel):
    items: list[ChequeOut]
    total: int


class PortfolioSummaryOut(BaseModel):
    """«Valores»: assets that are not available money, never added to the cash boxes or the bank."""

    portfolio_count: int
    portfolio_amount: Money
    in_transit_count: int
    in_transit_amount: Money
