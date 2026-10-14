"""The board of a settlement: every official total is computed here, never in the frontend.

justified = remittances + cash (count + additions not voided) + VERIFIED transfers + cheques + pending + expenses
difference = justified - expected (negative: shortage, positive: surplus)

Remittances (R7-3) are the cash a boca already handed over to a cash box during the day, in effect (not
reversed); a delivery settlement has none. The final count is what stays physically in the boca after them.

DECLARED and REJECTED transfers are shown but never justify money (Enzo's R2-P3). Cheques (R4-1) count
when they justify (a physical one confirmed in hand, a verified ECHEQ, not voided); a DECLARED ECHEQ is
counted apart because it blocks the close (Enzo's R4 decision D-K). Pending collections (R3-1) count with
their load, not voided: what the next close publishes. Voided additions, expenses and pendings do not count.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.settlements.cheques.models import Cheque
from app.modules.settlements.delivery.models import (
    CashAddition,
    CashCountLine,
    Expense,
    Settlement,
    SettlementTransfer,
)
from app.modules.settlements.outlet_settlements.models import OutletRemittance
from app.modules.settlements.pending.models import PendingCollection

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
    # An OUTLET settlement whose Store Sys amount is not loaded yet (R7-2): `expected` is 0 here and the
    # difference means nothing until it is loaded; the close requires it.
    expected_missing: bool = False
    by_box: dict[int, BoxTotals] = field(default_factory=lambda: defaultdict(BoxTotals))
    transfers: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    declared_transfers_count: int = 0
    expenses: Decimal = ZERO
    cheques: Decimal = ZERO
    unverified_echeqs_count: int = 0
    pending: Decimal = ZERO
    # Remittances of a boca in effect (R7-3); always 0 for a delivery settlement.
    remitted: Decimal = ZERO

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
        return (
            self.remitted + self.cash_total + self.transfers["VERIFIED"] + self.cheques + self.pending + self.expenses
        )

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
        sid: Totals(expected=ZERO if expected is None else expected, expected_missing=expected is None)
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
    for expense_settlement_id, amount in expenses:
        if expense_settlement_id is not None:  # always: filtered by settlement
            result[expense_settlement_id].expenses = amount
    pending = db.execute(
        select(PendingCollection.settlement_id, func.sum(PendingCollection.amount))
        .where(PendingCollection.settlement_id.in_(ids), PendingCollection.voided_at.is_(None))
        .group_by(PendingCollection.settlement_id)
    )
    for pending_sid, amount in pending:
        if pending_sid is not None:  # filtered by settlement: always
            result[pending_sid].pending = amount
    cheques = db.execute(
        select(Cheque.settlement_id, func.sum(Cheque.amount))
        .where(
            Cheque.settlement_id.in_(ids),
            Cheque.voided_at.is_(None),
            or_(Cheque.kind == "PHYSICAL", Cheque.verification_status == "VERIFIED"),
        )
        .group_by(Cheque.settlement_id)
    )
    for owner, amount in cheques:
        if owner is not None:  # filtered by settlement: always set
            result[owner].cheques = amount
    unverified = db.execute(
        select(Cheque.settlement_id, func.count())
        .where(
            Cheque.settlement_id.in_(ids),
            Cheque.voided_at.is_(None),
            Cheque.kind == "ECHEQ",
            Cheque.verification_status == "DECLARED",
        )
        .group_by(Cheque.settlement_id)
    )
    for owner, count in unverified:
        if owner is not None:
            result[owner].unverified_echeqs_count = count
    remitted = db.execute(
        select(OutletRemittance.settlement_id, func.sum(OutletRemittance.amount))
        .where(OutletRemittance.settlement_id.in_(ids), OutletRemittance.reversal_operation_id.is_(None))
        .group_by(OutletRemittance.settlement_id)
    )
    for owner, amount in remitted:
        result[owner].remitted = amount
    return result
