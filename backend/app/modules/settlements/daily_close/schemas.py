"""Responses of «Cierre del día» (R9-2). Amounts travel as decimal strings with two decimals."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.modules.settlements.cash.schemas import Money

AccountKind = Literal["CASH_BOX", "BANK"]
FinancialState = Literal["NOT_CLOSED", "CLOSED", "BEING_CORRECTED"]


class CategoryOut(BaseModel):
    """Operations of one type (or document origin) in one direction of one account. A reversal is its own category,
    named after what it reverses (`reverses`), never netted against the original."""

    key: str
    label: str
    reverses: str | None = None
    operations: int
    amount: Money


class AccountDayOut(BaseModel):
    account_id: int
    name: str
    kind: AccountKind
    # Today's state, secondary: the day is shown whatever the account is now.
    active: bool
    opening_balance: Money
    inflows: Money
    outflows: Money
    net: Money
    closing_balance: Money
    # opening + net = closing, the closing balance summed on its own from the ledger. False is an invariant error.
    reconciled: bool
    inflow_categories: list[CategoryOut]
    outflow_categories: list[CategoryOut]


class KindTotalsOut(BaseModel):
    kind: AccountKind
    opening_balance: Money
    inflows: Money
    outflows: Money
    net: Money
    closing_balance: Money


class ExpectedOriginOut(BaseModel):
    source: Literal["MANUAL", "STORE_SYS"]
    # Loaded before R9-1 («Manual · anterior a integración Store Sys»).
    legacy: bool
    external_reference: str | None = None
    revision: int | None = None


class SettlementDayOut(BaseModel):
    """One settlement of the day. The figures are those of the close in effect: a settlement in correction shows the
    close Cajas still holds, never its draft; an open one has none."""

    id: int
    type: Literal["DELIVERY", "OUTLET"]
    name: str
    sequence_number: int
    financial_state: FinancialState
    being_corrected: bool
    expected_missing: bool
    expected_update_pending: bool
    close_kind: Literal["FINANCIAL", "ADMINISTRATIVE_ZERO"] | None
    expected_amount: Money | None
    expected_origin: ExpectedOriginOut | None
    justified: Money | None
    difference: Money | None


class SettlementsDayOut(BaseModel):
    total: int
    open: int
    closed: int
    being_corrected: int
    without_expected: int
    store_sys_pending: int
    items: list[SettlementDayOut]


class ChequesDayOut(BaseModel):
    """«Cartera de cheques al final del día»: positions summed from the ledger up to the date; the documents of the
    day that are in effect today. A cheque's past states are not stored, so nothing else is reconstructed."""

    portfolio_count: int
    portfolio_amount: Money
    in_transit_count: int
    in_transit_amount: Money
    deposited_count: int
    deposited_amount: Money
    credited_count: int
    credited_amount: Money
    rejected_count: int
    rejected_amount: Money
    returned_count: int
    returned_amount: Money


class DailyCloseOut(BaseModel):
    """A calculated view of the business day: never signed, frozen nor stored; always recalculated."""

    business_date: date
    calculated_at: datetime
    accounts: list[AccountDayOut]
    totals: list[KindTotalsOut]
    reconciled: bool
    # Without `settlements.employee_finance`, employee movements are grouped as reserved.
    employee_movements_reserved: bool
    settlements: SettlementsDayOut
    cheques: ChequesDayOut
