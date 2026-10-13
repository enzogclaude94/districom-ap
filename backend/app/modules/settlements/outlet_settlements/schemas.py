"""Requests and responses of the boca de expendio settlements API (R7-2). Amounts travel as decimal strings.

The board of a boca settlement is the same as a delivery's (`delivery.schemas`): count, additions,
transfers, expenses, cheques, totals, difference, close in effect. What changes is who renders (a boca, not
a driver) and that its expected amount may be unknown (NULL) or 0 while it is loaded.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.settlements.cash.schemas import Money
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
    # The Store Sys amount is not loaded yet: `expected` reads 0 and the difference means nothing until it is.
    expected_missing: bool


class ResponsibleSuggestion(BaseModel):
    """A responsable of the boca on the settlement's business day: a suggestion to absorb a difference,
    never an automatic split, never preloaded with an amount (Enzo's D3)."""

    employee_id: int
    full_name: str


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
    # Null while the expected amount is not loaded.
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


class OutletSettlementsSummary(BaseModel):
    """What the ficha of a boca shows Rendiciones users."""

    outlet_id: int
    open_count: int
