"""What each operation type may do: minimum role and the shape of its legs (ADR 007, Enzo's R1 decisions).

Checked by `ledger.service` before anything is written, so no caller can post, say, an adjustment
without being admin or a "deposit" that moves money between two cash boxes.
"""

from dataclasses import dataclass

CASH_AND_BANK = frozenset({"CASH_BOX", "BANK"})
# Where a payment to a third party goes, by concept (R6, Enzo's decision D1).
PAYMENT_COUNTERPARTS = frozenset({"MERCHANDISE_PAYMENTS", "EXPENSE", "THIRD_PARTY_REFUNDS", "OTHER_PAYMENTS"})


@dataclass(frozen=True)
class OperationRule:
    min_role: str
    # Kinds every leg must belong to.
    kinds: frozenset[str]
    # Kind of which there must be exactly one leg (the counterpart), if any.
    counterpart: str | None = None
    # Required sign per kind: +1 value enters, -1 value leaves.
    signs: tuple[tuple[str, int], ...] = ()
    # Kinds of which, together, there must be exactly one leg (a counterpart that depends on the document).
    counterpart_one_of: frozenset[str] = frozenset()


RULES: dict[str, OperationRule] = {
    # Real money found when Rendiciones starts; never an invented income (D4).
    "OPENING_BALANCE": OperationRule(
        "admin", CASH_AND_BANK | {"OPENING_BALANCE"}, "OPENING_BALANCE", (("CASH_BOX", 1), ("BANK", 1))
    ),
    "CASH_TRANSFER": OperationRule("operator", frozenset({"CASH_BOX"})),
    "BANK_DEPOSIT": OperationRule("operator", CASH_AND_BANK, "BANK", (("CASH_BOX", -1), ("BANK", 1))),
    "BANK_WITHDRAWAL": OperationRule("operator", CASH_AND_BANK, "BANK", (("CASH_BOX", 1), ("BANK", -1))),
    "OWNER_CONTRIBUTION": OperationRule(
        "admin", CASH_AND_BANK | {"OWNER_EQUITY"}, "OWNER_EQUITY", (("CASH_BOX", 1), ("BANK", 1))
    ),
    "OWNER_WITHDRAWAL": OperationRule(
        "admin", CASH_AND_BANK | {"OWNER_EQUITY"}, "OWNER_EQUITY", (("CASH_BOX", -1), ("BANK", -1))
    ),
    # Admin only, with a mandatory reason, audited and reversible (D4).
    "ADJUSTMENT": OperationRule("admin", CASH_AND_BANK | {"ADJUSTMENT"}, "ADJUSTMENT"),
    # Close of a delivery settlement (R2-2): what the driver had to bring leaves the collections account;
    # cash, verified transfers, expenses and pending collections (R3) justify it; the difference goes to
    # employees or the company. Who may absorb a difference for the company is checked by the settlement,
    # which owns that rule.
    # R4-1: each cheque received (physical) or verified (ECHEQ) enters "Cartera de cheques" with its own leg.
    "SETTLEMENT_CLOSE": OperationRule(
        "operator",
        CASH_AND_BANK
        | {
            "DELIVERY_COLLECTIONS",
            "EXPENSE",
            "EMPLOYEE",
            "SETTLEMENT_DIFFERENCE",
            "PENDING_COLLECTIONS",
            "CHEQUE_PORTFOLIO",
        },
        "DELIVERY_COLLECTIONS",
        (
            ("DELIVERY_COLLECTIONS", -1),
            ("CASH_BOX", 1),
            ("BANK", 1),
            ("EXPENSE", 1),
            ("PENDING_COLLECTIONS", 1),
            ("CHEQUE_PORTFOLIO", 1),
        ),
    ),
    # A customer pays a pending collection (R3-2): it leaves "Pendientes de cobro" and enters the cash box
    # (CASH) or the bank (TRANSFER). The pending decides which one; exactly two legs.
    # R4-2: or with a cheque (method CHEQUE), which enters "Cartera de cheques".
    "PENDING_COLLECTION": OperationRule(
        "operator",
        CASH_AND_BANK | {"PENDING_COLLECTIONS", "CHEQUE_PORTFOLIO"},
        "PENDING_COLLECTIONS",
        (("PENDING_COLLECTIONS", -1), ("CASH_BOX", 1), ("BANK", 1), ("CHEQUE_PORTFOLIO", 1)),
    ),
    # Administrative resolution of a pending (R3-2): admin only, with a reason. The company's part goes to
    # "Incobrables", each employee's part to their internal account (the employee owes it).
    "PENDING_WRITE_OFF": OperationRule(
        "admin",
        frozenset({"PENDING_COLLECTIONS", "UNCOLLECTIBLE", "EMPLOYEE"}),
        "PENDING_COLLECTIONS",
        (("PENDING_COLLECTIONS", -1), ("UNCOLLECTIBLE", 1), ("EMPLOYEE", 1)),
    ),
    # Administrative entries of a cheque into the portfolio (Enzo's R4 decision D-B), admin only: a cheque
    # already held when Rendiciones starts, or a partner's contribution with a cheque. Never a generic
    # "extraordinary" entry: every value has a known economic cause.
    "CHEQUE_OPENING_BALANCE": OperationRule(
        "admin",
        frozenset({"OPENING_BALANCE", "CHEQUE_PORTFOLIO"}),
        "OPENING_BALANCE",
        (("OPENING_BALANCE", -1), ("CHEQUE_PORTFOLIO", 1)),
    ),
    "CHEQUE_OWNER_CONTRIBUTION": OperationRule(
        "admin",
        frozenset({"OWNER_EQUITY", "CHEQUE_PORTFOLIO"}),
        "OWNER_EQUITY",
        (("OWNER_EQUITY", -1), ("CHEQUE_PORTFOLIO", 1)),
    ),
    # The financial cycle of a cheque (R4-2). Each is the operation of its document (deposit slip, crediting,
    # rejection, return), which builds the exact legs; the database checks them against the document.
    # A deposit slip: per cheque, "Cartera de cheques" -X and "Cheques depositados" +X.
    "CHEQUE_DEPOSIT": OperationRule(
        "operator",
        frozenset({"CHEQUE_PORTFOLIO", "CHEQUES_IN_TRANSIT"}),
        None,
        (("CHEQUE_PORTFOLIO", -1), ("CHEQUES_IN_TRANSIT", 1)),
    ),
    # A crediting: per cheque, "Cheques depositados" -X; the bank +total.
    "CHEQUE_CREDIT": OperationRule(
        "operator",
        frozenset({"CHEQUES_IN_TRANSIT", "BANK"}),
        "BANK",
        (("CHEQUES_IN_TRANSIT", -1), ("BANK", 1)),
    ),
    # A rejection: "Cheques depositados" -X (in transit) or the bank -X (after crediting), and a new pending.
    "CHEQUE_REJECTION": OperationRule(
        "operator",
        frozenset({"CHEQUES_IN_TRANSIT", "BANK", "PENDING_COLLECTIONS"}),
        "PENDING_COLLECTIONS",
        (("CHEQUES_IN_TRANSIT", -1), ("BANK", -1), ("PENDING_COLLECTIONS", 1)),
    ),
    # A cheque of the portfolio given back (admin, with a reason): "Cartera de cheques" -X, a new pending.
    "CHEQUE_RETURN": OperationRule(
        "admin",
        frozenset({"CHEQUE_PORTFOLIO", "PENDING_COLLECTIONS"}),
        "PENDING_COLLECTIONS",
        (("CHEQUE_PORTFOLIO", -1), ("PENDING_COLLECTIONS", 1)),
    ),
    # An expense paid directly from one cash box or the bank (R5-3): the account -X, «Gastos operativos» +X.
    # Its document is the expense, which builds the two legs; the database checks them against it. An
    # ordinary debit: the bank never goes into an overdraft for it (not an EXTERNAL_BANK_DEBIT).
    "DIRECT_EXPENSE": OperationRule(
        "operator", CASH_AND_BANK | {"EXPENSE"}, "EXPENSE", (("CASH_BOX", -1), ("BANK", -1), ("EXPENSE", 1))
    ),
    # A payment made to a third party (R6-1): each item leaves its cash box or bank account, or (R6-2) each
    # cheque leaves "Cartera de cheques" with its own leg; ONE counter-leg for the whole amount enters the
    # technical account of its concept (merchandise, expense, refund, other). Its document is the payment,
    # which builds the legs; the database checks them against it. An ordinary debit: the bank never goes
    # into an overdraft for it (not an EXTERNAL_BANK_DEBIT).
    "PAYMENT": OperationRule(
        "operator",
        CASH_AND_BANK | PAYMENT_COUNTERPARTS | {"CHEQUE_PORTFOLIO"},
        None,
        (
            ("CASH_BOX", -1),
            ("BANK", -1),
            ("CHEQUE_PORTFOLIO", -1),
            ("MERCHANDISE_PAYMENTS", 1),
            ("EXPENSE", 1),
            ("THIRD_PARTY_REFUNDS", 1),
            ("OTHER_PAYMENTS", 1),
        ),
        PAYMENT_COUNTERPARTS,
    ),
}
CHEQUE_CYCLE_TYPES = frozenset({"CHEQUE_DEPOSIT", "CHEQUE_CREDIT", "CHEQUE_REJECTION", "CHEQUE_RETURN"})
# Operations that belong to a source document: only that document's flow corrects them, never Cajas.
DOCUMENT_TYPES = frozenset(
    {
        "SETTLEMENT_CLOSE",
        "PENDING_COLLECTION",
        "PENDING_WRITE_OFF",
        "CHEQUE_OPENING_BALANCE",
        "CHEQUE_OWNER_CONTRIBUTION",
        "DIRECT_EXPENSE",
        "PAYMENT",
    }
    | CHEQUE_CYCLE_TYPES
)
# Document operations undone with `reverse_document_operation` (a settlement close is replaced instead).
REVERSIBLE_DOCUMENT_TYPES = frozenset(
    {"PENDING_COLLECTION", "PENDING_WRITE_OFF", "DIRECT_EXPENSE", "PAYMENT"} | CHEQUE_CYCLE_TYPES
)
# «Banco en descubierto» (Enzo's R4-2 policy): the only debits a bank account books even if they create or
# deepen an overdraft. A rejection of a cheque already credited is an external event, not a decision of the
# company. Every other debit of a bank (withdrawal, transfer out, a reversal...) keeps every end-of-day
# balance from its date on >= 0; incomes are never limited. The database applies the same rule (0013).
EXTERNAL_BANK_DEBITS = frozenset({"CHEQUE_REJECTION"})
# REVERSAL has no rule here: it is only built by `reverse_operation` from the original's legs.
REVERSAL_MIN_ROLE = "admin"


def leg_shape_error(rule: OperationRule, kinds: list[str], signs: list[int]) -> str | None:
    """Why legs with these account kinds and signs do not fit the rule, or None if they do."""
    if any(kind not in rule.kinds for kind in kinds):
        return "Una de las cuentas no corresponde a este tipo de operación."
    if rule.counterpart is not None and kinds.count(rule.counterpart) != 1:
        return "La operación debe tener exactamente un movimiento en la cuenta de contrapartida."
    if rule.counterpart_one_of and sum(kind in rule.counterpart_one_of for kind in kinds) != 1:
        return "La operación debe tener exactamente un movimiento en la cuenta de contrapartida."
    required = dict(rule.signs)
    for kind, sign in zip(kinds, signs, strict=True):
        if kind in required and required[kind] != sign:
            return "El sentido de uno de los movimientos no corresponde a este tipo de operación."
    return None
