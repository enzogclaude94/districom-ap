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
    # In the portfolio past its deadline: it can no longer be deposited, only returned (R4-2 decision A).
    presentation_expired: bool
    # Days left to present it, while it is in the portfolio and not expired (R4-3); else null.
    expires_in_days: int | None = None
    # Within `settlements.cheque_expiring_soon_days` of its deadline: «Vence en N días».
    expiring_soon: bool = False
    # Where its value is now, for people: «Cartera de cheques», «Depositado en Banco Nación»…
    location_label: str | None = None
    # The deposit in effect (DEPOSITED, CREDITED, or REJECTED after it) and the crediting in effect.
    deposit_id: int | None = None
    deposit_business_date: date | None = None
    deposit_bank_account_id: int | None = None
    deposit_bank_account_name: str | None = None
    credit_id: int | None = None
    credit_business_date: date | None = None
    # The payment in effect that delivered it (DELIVERED, R6-2): to whom and on which business date.
    delivery_payment_id: int | None = None
    delivery_third_party_id: int | None = None
    delivery_third_party_name: str | None = None
    delivery_business_date: date | None = None
    drawer_name: str
    drawer_tax_id: str | None
    delivered_by_name: str
    origin_type: str
    settlement_id: int | None
    # The pending collection a customer paid with it (origin PENDING_RECEIPT, R4-2).
    origin_pending_id: int | None
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
    # DELIVERED / RECOVERED_FROM_PAYMENT: the payment (R6-2).
    payment_id: int | None = None
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
    # Its financial cycle (R4-2): deposits, creditings, rejections, returns and the receipt it paid, oldest first.
    documents: list["ChequeDocumentOut"]


class ChequePage(BaseModel):
    items: list[ChequeOut]
    total: int


class PortfolioSummaryOut(BaseModel):
    """«Valores»: assets that are not available money, never added to the cash boxes or the bank."""

    portfolio_count: int
    portfolio_amount: Money
    in_transit_count: int
    in_transit_amount: Money


# --- the financial cycle (R4-2) -------------------------------------------------------------------

ChequeIds = Annotated[list[int], Field(min_length=1, max_length=100)]
Notes = Annotated[str, Field(max_length=1000)]


class DepositIn(StrictIn):
    """A deposit slip: cheques of the portfolio taken to one bank account (operator)."""

    # Generated once per user action and resent on retries: a retry never deposits twice.
    client_request_id: UUID
    bank_account_id: int
    cheque_ids: ChequeIds
    # When the slip was presented; today when omitted. Never in the future.
    business_date: date | None = None
    slip_number: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    notes: Notes | None = None


class CreditIn(StrictIn):
    """The bank credited deposited cheques (operator): one or several, as the statement shows them."""

    client_request_id: UUID
    bank_account_id: int
    cheque_ids: ChequeIds
    # The date of the statement; today when omitted.
    business_date: date | None = None
    notes: Notes | None = None


class RejectionIn(StrictIn):
    """The bank rejected a deposited cheque (operator or admin), with the bank's reason. A new pending is
    owed by who delivered it; its responsible is the driver of the cheque's settlement unless said."""

    client_request_id: UUID
    reason: Reason
    business_date: date | None = None
    responsible_employee_id: int | None = None


class ReturnIn(RejectionIn):
    """A cheque of the portfolio given back to who delivered it (admin, with a reason)."""


class CycleReversalIn(StrictIn):
    """Reverse a deposit, a crediting, a rejection or a return: it was wrong. Admin only, with a reason;
    it keeps the original's business date. Idempotent."""

    client_request_id: UUID
    reason: Reason


class DocumentReversalOut(BaseModel):
    operation_id: int
    reversed_by: int
    reversed_by_name: str
    reversed_at: datetime
    reason: str


class CycleItemOut(BaseModel):
    cheque_id: int
    amount: Money
    kind: str
    cheque_number: str
    bank_name: str
    delivered_by_name: str
    state: State
    state_label: str


class DepositOut(BaseModel):
    id: int
    bank_account_id: int
    bank_account_name: str
    business_date: date
    slip_number: str | None
    notes: str | None
    total: Money
    items: list[CycleItemOut]
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: DocumentReversalOut | None


class CreditOut(BaseModel):
    id: int
    bank_account_id: int
    bank_account_name: str
    business_date: date
    notes: str | None
    total: Money
    items: list[CycleItemOut]
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: DocumentReversalOut | None


class RejectionOut(BaseModel):
    id: int
    cheque_id: int
    # IN_TRANSIT (the bank never credited it) or AFTER_CREDIT (it debited the bank).
    stage: str
    business_date: date
    reason: str
    bank_account_id: int | None
    # The new pending collection the rejection created.
    pending_id: int
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: DocumentReversalOut | None


class ReturnOut(BaseModel):
    id: int
    cheque_id: int
    business_date: date
    reason: str
    pending_id: int
    operation_id: int
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal: DocumentReversalOut | None


class ChequeDocumentOut(BaseModel):
    """One document of a cheque's cycle, as its history shows it."""

    type: Literal["DEPOSIT", "CREDIT", "REJECTION", "RETURN", "PENDING_RECEIPT", "PAYMENT"]
    id: int
    business_date: date
    operation_id: int
    reversed: bool
    reversal_operation_id: int | None
    # The pending a rejection or a return created, or the one a receipt collected.
    pending_id: int | None
    # PAYMENT (R6-2): the third party it was delivered to.
    third_party_id: int | None = None
    third_party_name: str | None = None


class ChequeReceiptIn(InstrumentIn):
    """A customer pays a pending collection with a cheque (R4-2). A physical cheque, confirmed in hand, pays it
    at once and enters the portfolio; an ECHEQ is declared and pays nothing until it is verified. Never above
    the pending's balance, never partially accepted."""

    client_request_id: UUID
    # When the customer gave it; today when omitted. Not before the pending's origin, not in the future.
    business_date: date | None = None


class PendingEcheqVerificationIn(StrictIn):
    """Verify the ECHEQ declared for a pending (it pays it then, if the balance still allows it), or reject its
    verification (with a reason). Decided once: a rejected ECHEQ is declared again as a new one."""

    status: Literal["VERIFIED", "VERIFICATION_REJECTED"]
    reason: Reason | None = None
    # The day it pays the pending when verified; today when omitted.
    business_date: date | None = None
