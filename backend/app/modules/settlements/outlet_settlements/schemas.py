"""Requests and responses of the boca de expendio settlements API (R7-2). Amounts travel as decimal strings.

The board of a boca settlement is the same as a delivery's (`delivery.schemas`): count, additions,
transfers, expenses, cheques, totals, difference, close in effect. What changes is who renders (a boca, not
a driver) and that its expected amount may be unknown (NULL) or 0 while it is loaded.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.settlements.cash.schemas import Amount, Money, Reason
from app.modules.settlements.cheques.schemas import ChequeOut
from app.modules.settlements.delivery import schemas as ds
from app.modules.settlements.pending.schemas import PendingOut

# The Store Sys amount of a boca: 0 is a valid amount (a day without sales), unlike a delivery's.
ExpectedAmount = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class OutletSettlementIn(ds.StrictIn):
    # Generated once per user action and resent on retries: a retry never creates "Rendición 2".
    client_request_id: UUID
    business_date: date
    outlet_id: int
    # Unknown while Store Sys has not given it: load it later, before closing.
    expected_amount: ExpectedAmount | None = None
    external_reference: str | None = Field(default=None, max_length=100)
    notes: ds.Text500 | None = None
    # A second (third...) settlement of the same boca and day needs the user's explicit confirmation.
    confirm_additional: bool = False


class OutletSettlementPatch(ds.SettlementPatch):
    """Fields of an OPEN boca settlement that can be corrected. Boca, date and number never change. The
    expected amount may be cleared (null) while it is open."""

    expected_amount: ExpectedAmount | None = None


class OutletSummary(ds.Summary):
    """The board of a boca (R7-3): what was handed over (`remitted`), what stayed and justifies (`cash_total`,
    transfers, cheques, expenses) and, while the settlement is OPEN, how far it is from the expected amount, never
    a shortage or surplus: the difference exists only once it is closed."""

    # The Store Sys amount is not loaded yet: `expected` reads 0 and the difference means nothing until it is.
    expected_missing: bool
    # Null while OPEN (never closed or being corrected): there is no difference until the close.
    difference: Money | None  # type: ignore[assignment]
    difference_kind: Literal["SHORTAGE", "SURPLUS", "BALANCED"] | None  # type: ignore[assignment]
    # expected - justified while that is >= 0; null without an expected amount.
    remaining: Money | None = None
    # justified - expected when the justified total already goes above it; null otherwise.
    justified_above_expected: Money | None = None
    # What justifies besides the remittances: final cash, verified transfers, cheques and expenses (R7-4).
    justified_other: Money = Decimal("0.00")
    # Remittances the close in effect consolidated; null without a financial close in effect. During a
    # correction it is what stays in effect until the new close, next to `remitted` (the current proposal).
    remitted_in_effect_close: Money | None = None
    # Expected $0 and nothing loaded: closing it is an administrative zero close, without financial movements.
    without_movements: bool = False


class ResponsibleSuggestion(BaseModel):
    """A responsable of the boca on the settlement's business day: a suggestion to absorb a difference,
    never an automatic split, never preloaded with an amount (Enzo's D3)."""

    employee_id: int
    full_name: str


# --- remittances (R7-3) ---------------------------------------------------------------------------


class RemittancePreviewIn(ds.StrictIn):
    amount: Amount
    # An active cash box (the screen proposes Caja principal); never a bank nor a technical account.
    destination_account_id: int
    # When it happened (with its time zone): not in the future and on the settlement's business date.
    delivered_at: datetime
    # Who carried the cash, optional; the screen suggests the boca's responsables of that day.
    delivered_by_employee_id: int | None = None
    reference: str | None = Field(default=None, max_length=100)


class RemittanceIn(RemittancePreviewIn):
    # Generated once per user action and resent on retries.
    client_request_id: UUID
    # Required when the remittances in effect would go above the expected amount (never blocked).
    confirm_above_expected: bool = False


class RemittanceReversalIn(ds.StrictIn):
    client_request_id: UUID
    reason: Reason


class RemittanceLegOut(BaseModel):
    # Null for the boca's «Entregas a cuenta» before its first remittance creates it.
    account_id: int | None
    account_name: str
    amount: Money


class RemittancePreviewOut(BaseModel):
    """What «Entregar efectivo» would publish, for the user to confirm before it does."""

    legs: list[RemittanceLegOut]
    remitted_before: Money
    remitted_after: Money
    expected: Money | None
    # How much the remittances in effect would go above the expected amount; null when they would not.
    excess: Money | None
    warning: str | None


class RemittanceOut(BaseModel):
    id: int
    amount: Money
    delivered_at: datetime
    destination_account_id: int
    destination_account_name: str
    delivered_by_employee_id: int | None
    delivered_by_name: str | None
    reference: str | None
    operation_id: int | None
    # IN_EFFECT or REVERSED.
    status: Literal["IN_EFFECT", "REVERSED"]
    # Consolidated by the close in effect (a reversed one may still be, until the new close of a correction).
    consolidated: bool
    created_by: int
    created_by_name: str
    created_at: datetime
    reversal_operation_id: int | None
    reversed_by: int | None
    reversed_by_name: str | None
    reversed_at: datetime | None
    reversal_reason: str | None


class OutletSettlementItem(BaseModel):
    id: int
    business_date: date
    outlet_id: int
    outlet_code: str
    outlet_name: str
    sequence_number: int
    expected_amount: Money | None
    external_reference: str | None
    status: str
    version: int
    financial_state: ds.FinancialState
    administrative_zero: bool
    created_at: datetime
    justified: Money
    # Null while the settlement is OPEN (no difference until the close, R7-3) or the expected amount is not loaded.
    difference: Money | None


class OutletSettlementPage(BaseModel):
    items: list[OutletSettlementItem]
    total: int


class OutletSettlementOut(BaseModel):
    id: int
    type: str
    business_date: date
    outlet_id: int
    outlet_code: str
    outlet_name: str
    # The boca was deactivated after this settlement opened: it is still loaded, closed and corrected.
    outlet_active: bool
    sequence_number: int
    expected_amount: Money | None
    expected_source: str
    external_reference: str | None
    notes: str | None
    status: str
    version: int
    created_by: int
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    # Other settlements of the same boca and day.
    same_day_settlement_ids: list[int]
    responsible_suggestions: list[ResponsibleSuggestion]
    count_lines: list[ds.CountLineOut]
    additions: list[ds.AdditionOut]
    transfers: list[ds.TransferOut]
    expenses: list[ds.ExpenseOut]
    # Always empty: a boca has no pending collections (kept so the board has one shape).
    pending_collections: list[PendingOut]
    cheques: list[ChequeOut] = []
    # Partial deliveries of the day (R7-3), reversed ones included.
    remittances: list[RemittanceOut] = []
    summary: OutletSummary
    financial_state: ds.FinancialState
    financial_state_detail: str
    close_in_effect: ds.CloseRef | None
    closed_by: int | None
    closed_by_name: str | None
    closed_at: datetime | None
    reopened_by: int | None
    reopened_by_name: str | None
    reopened_at: datetime | None
    reopen_reason: str | None
    current_difference: ds.DifferenceOut | None


class OutletClosingPreviewOut(ds.ClosingPreviewOut):
    summary: OutletSummary
    responsible_suggestions: list[ResponsibleSuggestion]


class OutletSettlementRef(BaseModel):
    id: int
    business_date: date
    sequence_number: int
    status: str
    financial_state: ds.FinancialState
    administrative_zero: bool


class OutletSettlementsSummary(BaseModel):
    """What the ficha of a boca shows Rendiciones users (R7-4): its operation, never a replacement of the board."""

    outlet_id: int
    open_count: int
    # OPEN settlements (never closed or being corrected), oldest first.
    open_settlements: list[OutletSettlementRef] = []
    # The latest settlement of the boca, whatever its state; null when it never had one.
    last_settlement: OutletSettlementRef | None = None
    # Cash the boca already handed over (remittances in effect) that no close in effect consolidated yet.
    remitted_not_consolidated: Money = Decimal("0.00")


# --- daily board (R7-4) ---------------------------------------------------------------------------


class OutletBoardSettlement(OutletSettlementRef):
    """The figures of one settlement on the daily board. Never mixed with another settlement's."""

    expected_amount: Money | None
    remitted: Money
    cash_total: Money
    transfers_verified: Money
    cheques: Money
    expenses: Money
    justified: Money
    # While OPEN: expected - justified when >= 0 (null without an expected amount).
    remaining: Money | None
    # While OPEN: justified - expected when the justified total already goes above it.
    justified_above_expected: Money | None
    # Only when CLOSED: the difference of the close in effect.
    difference: Money | None
    difference_kind: Literal["SHORTAGE", "SURPLUS", "BALANCED"] | None


class OutletBoardRow(BaseModel):
    outlet_id: int
    outlet_code: str
    outlet_name: str
    outlet_active: bool
    # Every settlement of the boca that day, by number (with their figures).
    settlements: list[OutletBoardSettlement]
    # The one the board shows: the first OPEN (never closed or being corrected) by number, else the last one;
    # null for a boca without a settlement that day.
    current_id: int | None


class OutletBoardCounts(BaseModel):
    without_settlement: int
    open: int
    being_corrected: int
    closed: int


class OutletBoardOut(BaseModel):
    """Rendiciones > Bocas for one business day: every active boca, plus an inactive one with settlements that
    day, by code."""

    business_date: date
    rows: list[OutletBoardRow]
    counts: OutletBoardCounts


# --- history (R7-4) -------------------------------------------------------------------------------


class ClosingRemittanceOut(BaseModel):
    remittance_id: int
    amount: Money


class OutletClosingOut(ds.ClosingOut):
    # The remittances this close consolidated, for their amount (empty for a zero close).
    remittances: list[ClosingRemittanceOut] = Field(default_factory=list)


class ExpectedChangeOut(BaseModel):
    """A load or correction of the Store Sys amount, from the audit trail."""

    before: Money | None
    after: Money | None
    changed_by: int | None
    changed_by_name: str | None
    changed_at: datetime


class OutletHistoryOut(ds.HistoryOut):
    closings: list[OutletClosingOut]  # type: ignore[assignment]
    expected_changes: list[ExpectedChangeOut]
