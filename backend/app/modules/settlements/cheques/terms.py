"""When a cheque can be presented to the bank, by its payment type (Enzo's R4 decision on COMMON/DEFERRED and
R4-1 adjustment 2). These are rules of the instrument (Ley de Cheques 24.452), not preferences of the
business: fixed here, never a setting anyone can change to let an expired cheque through.

- COMMON: payable from its issue date (`issue_date`); it has no payment date. Presented at the latest
  `issue_date + 30 days`.
- DEFERRED: payable from its payment date (`payment_date`, required), with
  `issue_date < payment_date <= issue_date + 360 days`; it can never be deposited before it. Presented at
  the latest `payment_date + 30 days`.

After its deadline the cheque is expired (`cheque_presentation_expired`). The database repeats the
360-day bound (ck_cheques_payment_date_by_type).
"""

from datetime import date, timedelta

from app.core.errors import AppError

PRESENTATION_DAYS = 30
MAX_DEFERRED_DAYS = 360


def payable_from(payment_type: str, issue_date: date, payment_date: date | None) -> date:
    if payment_type == "DEFERRED":
        if payment_date is None:
            raise ValueError("a deferred cheque always has a payment date")
        return payment_date
    return issue_date


def presentation_deadline(payment_type: str, issue_date: date, payment_date: date | None) -> date:
    return payable_from(payment_type, issue_date, payment_date) + timedelta(days=PRESENTATION_DAYS)


def check_presentable(payment_type: str, issue_date: date, payment_date: date | None, on: date) -> None:
    """The cheque has not expired for presentation on `on`."""
    deadline = presentation_deadline(payment_type, issue_date, payment_date)
    if on > deadline:
        raise AppError(
            422,
            "cheque_presentation_expired",
            f"El cheque venció para su presentación el {deadline:%d/%m/%Y}: ya no se puede cobrar.",
            {"deadline": deadline.isoformat()},
        )


def check_dates(*, payment_type: str, issue_date: date, payment_date: date | None, received_on: date) -> None:
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
        latest = issue_date + timedelta(days=MAX_DEFERRED_DAYS)
        if payment_date > latest:
            raise AppError(
                422,
                "deferred_term_exceeded",
                f"Un cheque de pago diferido vence como máximo {MAX_DEFERRED_DAYS} días después de su emisión "
                f"(el {latest:%d/%m/%Y}).",
                {"field": "payment_date", "max_payment_date": latest.isoformat()},
            )
    if issue_date > received_on:
        raise AppError(
            422,
            "invalid_cheque_date",
            "La fecha de emisión no puede ser posterior a la de recepción.",
            {"field": "issue_date", "received_on": received_on.isoformat()},
        )
    check_presentable(payment_type, issue_date, payment_date, received_on)
