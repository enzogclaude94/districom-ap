"""When a cheque can be presented to the bank, by its payment type (Enzo's R4 decision on COMMON/DEFERRED).

Never one reading of "the cheque's date" for both types:

- COMMON: payable from its issue date (`issue_date`); it has no payment date.
- DEFERRED: payable from its payment date (`payment_date`, required, later than the issue date); it can
  never be deposited for collection before it.

Either way it must be presented within `settlements.cheque_presentation_days` (30 by default, Ley de
Cheques 24.452) from the date it becomes payable: after that it is expired and cannot be collected.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.settings.service import get_setting

PRESENTATION_DAYS_KEY = "settlements.cheque_presentation_days"


def presentation_days(db: Session) -> int:
    return int(get_setting(db, PRESENTATION_DAYS_KEY))


def payable_from(payment_type: str, issue_date: date, payment_date: date | None) -> date:
    if payment_type == "DEFERRED":
        if payment_date is None:
            raise ValueError("a deferred cheque always has a payment date")
        return payment_date
    return issue_date


def presentation_deadline(payment_type: str, issue_date: date, payment_date: date | None, days: int) -> date:
    return payable_from(payment_type, issue_date, payment_date) + timedelta(days=days)


def check_dates(
    *, payment_type: str, issue_date: date, payment_date: date | None, received_on: date, days: int
) -> None:
    """The dates of an instrument make sense for its type and it was not expired when we received it."""
    if payment_type == "COMMON":
        if payment_date is not None:
            raise AppError(
                422,
                "payment_date_not_allowed",
                "Un cheque común es pagadero desde su emisión: no lleva fecha de pago. Si tiene una fecha de "
                "pago posterior, es de pago diferido.",
            )
    else:
        if payment_date is None:
            raise AppError(422, "payment_date_required", "Un cheque de pago diferido necesita su fecha de pago.")
        if payment_date <= issue_date:
            raise AppError(
                422,
                "invalid_cheque_date",
                "La fecha de pago de un cheque diferido es posterior a su fecha de emisión.",
                {"field": "payment_date"},
            )
    if issue_date > received_on:
        raise AppError(
            422,
            "invalid_cheque_date",
            "La fecha de emisión no puede ser posterior a la de recepción.",
            {"field": "issue_date", "received_on": received_on.isoformat()},
        )
    deadline = presentation_deadline(payment_type, issue_date, payment_date, days)
    if received_on > deadline:
        raise AppError(
            422,
            "cheque_presentation_expired",
            f"El cheque venció para su presentación el {deadline:%d/%m/%Y}: ya no se puede cobrar.",
            {"deadline": deadline.isoformat()},
        )
