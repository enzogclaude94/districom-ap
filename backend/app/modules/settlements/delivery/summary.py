"""The board of a settlement: every official total is computed here, never in the frontend.

justified = cash (count + additions not voided) + VERIFIED transfers + cheques + pending + expenses
difference = justified - expected (negative: shortage, positive: surplus)

DECLARED and REJECTED transfers are shown but never justify money (Enzo's R2-P3). Cheques (R4) and
pending collections (R3) are zero until their phase. Voided additions and expenses do not count.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.settlements.delivery.models import (
    CashAddition,
    CashCountLine,
    Expense,
    Settlement,
    SettlementTransfer,
)

ZERO = Decimal("0.00")
DifferenceKind = Literal["SHORTAGE", "SURPLUS", "BALANCED"]


@dataclass
class BoxTotals:
    counted: Decimal = ZERO
    additions: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.counted + self.additions


@dataclass
class Totals:
    expected: Decimal
    by_box: dict[int, BoxTotals] = field(default_factory=lambda: defaultdict(BoxTotals))
    transfers: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    declared_transfers_count: int = 0
    expenses: Decimal = ZERO
    cheques: Decimal = ZERO
    pending: Decimal = ZERO

    @property
    def cash_counted(self) -> Decimal:
        return sum((box.counted for box in self.by_box.values()), ZERO)

    @property
    def cash_additions(self) -> Decimal:
        return sum((box.additions for box in self.by_box.values()), ZERO)

    @property
    def cash_total(self) -> Decimal:
        return self.cash_counted + self.cash_additions

    @property
    def justified(self) -> Decimal:
        return self.cash_total + self.transfers["VERIFIED"] + self.cheques + self.pending + self.expenses

    @property
    def difference(self) -> Decimal:
        return self.justified - self.expected

    @property
    def difference_kind(self) -> DifferenceKind:
        if self.difference < 0:
            return "SHORTAGE"
        return "SURPLUS" if self.difference > 0 else "BALANCED"


def totals(db: Session, settlement_ids: Sequence[int]) -> dict[int, Totals]:
    """Totals of each settlement, in a fixed number of queries whatever the number of settlements."""
    if not settlement_ids:
        return {}
    ids = list(settlement_ids)
    result = {
        sid: Totals(expected=expected)
        for sid, expected in db.execute(select(Settlement.id, Settlement.expected_amount).where(Settlement.id.in_(ids)))
    }
    counted = db.execute(
        select(
            CashCountLine.settlement_id,
            CashCountLine.destination_account_id,
            func.sum(CashCountLine.denomination_value * CashCountLine.quantity),
        )
        .where(CashCountLine.settlement_id.in_(ids))
        .group_by(CashCountLine.settlement_id, CashCountLine.destination_account_id)
    )
    for sid, box, amount in counted:
        result[sid].by_box[box].counted = amount
    added = db.execute(
        select(CashAddition.settlement_id, CashAddition.destination_account_id, func.sum(CashAddition.amount))
        .where(CashAddition.settlement_id.in_(ids), CashAddition.voided_at.is_(None))
        .group_by(CashAddition.settlement_id, CashAddition.destination_account_id)
    )
    for sid, box, amount in added:
        result[sid].by_box[box].additions = amount
    transfers = db.execute(
        select(
            SettlementTransfer.settlement_id,
            SettlementTransfer.status,
            func.sum(SettlementTransfer.amount),
            func.count(),
        )
        .where(SettlementTransfer.settlement_id.in_(ids))
        .group_by(SettlementTransfer.settlement_id, SettlementTransfer.status)
    )
    for sid, status, amount, count in transfers:
        result[sid].transfers[status] = amount
        if status == "DECLARED":
            result[sid].declared_transfers_count = count
    expenses = db.execute(
        select(Expense.settlement_id, func.sum(Expense.amount))
        .where(Expense.settlement_id.in_(ids), Expense.voided_at.is_(None))
        .group_by(Expense.settlement_id)
    )
    for sid, amount in expenses:
        result[sid].expenses = amount
    return result
