"""Rules of cheques that need no database (R4-1): dates by payment type [8] [9] [10], the identity strategy
[11] [13] [16] and the derived states [6] [26], checked by hand."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.modules.settlements.cheques import identity, state, terms

ISSUED = date(2026, 10, 1)


@pytest.mark.parametrize(
    ("payment_type", "payment_date", "received_on", "error"),
    [
        ("COMMON", None, date(2026, 10, 5), None),
        ("COMMON", None, date(2026, 10, 31), None),  # last day of its 30 days
        ("COMMON", None, date(2026, 11, 1), "cheque_presentation_expired"),
        ("COMMON", date(2026, 12, 1), date(2026, 10, 5), "payment_date_not_allowed"),
        ("DEFERRED", None, date(2026, 10, 5), "payment_date_required"),
        ("DEFERRED", ISSUED, date(2026, 10, 5), "invalid_cheque_date"),
        ("DEFERRED", date(2026, 12, 1), date(2026, 10, 5), None),
        # A deferred cheque counts its 30 days from its payment date, not from its issue.
        ("DEFERRED", date(2026, 10, 10), date(2026, 11, 9), None),
        ("DEFERRED", date(2026, 10, 10), date(2026, 11, 10), "cheque_presentation_expired"),
        ("COMMON", None, date(2026, 9, 30), "invalid_cheque_date"),  # received before issued
        # A deferred cheque is payable at most 360 days after its issue: 2026-10-01 + 360 = 2027-09-26.
        ("DEFERRED", date(2027, 9, 26), date(2026, 10, 5), None),
        ("DEFERRED", date(2027, 9, 27), date(2026, 10, 5), "deferred_term_exceeded"),
        # At its 360-day limit, it still has its 30 days of presentation from the payment date.
        ("DEFERRED", date(2027, 9, 26), date(2027, 10, 26), None),
        ("DEFERRED", date(2027, 9, 26), date(2027, 10, 27), "cheque_presentation_expired"),
    ],
)
def test_dates_by_payment_type(
    payment_type: str, payment_date: date | None, received_on: date, error: str | None
) -> None:
    def check() -> None:
        terms.check_dates(
            payment_type=payment_type, issue_date=ISSUED, payment_date=payment_date, received_on=received_on
        )

    if error is None:
        check()
    else:
        with pytest.raises(AppError) as caught:
            check()
        assert caught.value.code == error


def test_payable_from_and_deadline_depend_on_the_type() -> None:
    assert terms.payable_from("COMMON", ISSUED, None) == ISSUED
    assert terms.payable_from("DEFERRED", ISSUED, date(2026, 12, 1)) == date(2026, 12, 1)
    assert terms.presentation_deadline("COMMON", ISSUED, None) == date(2026, 10, 31)
    assert terms.presentation_deadline("DEFERRED", ISSUED, date(2026, 12, 1)) == date(2026, 12, 31)


def test_terms_are_rules_of_the_instrument_not_settings() -> None:
    """Enzo's R4-1 adjustment 2: 30 days of presentation and 360 of deferral, fixed; no setting moves them."""
    from app.modules.settlements.settings import SETTINGS

    assert (terms.PRESENTATION_DAYS, terms.MAX_DEFERRED_DAYS) == (30, 360)
    assert not any("cheque" in spec.key for spec in SETTINGS.specs)


@pytest.mark.parametrize(
    ("payment_type", "payment_date", "on", "expired"),
    [
        ("COMMON", None, date(2026, 10, 31), False),
        ("COMMON", None, date(2026, 11, 1), True),
        ("DEFERRED", date(2027, 1, 15), date(2027, 2, 14), False),
        ("DEFERRED", date(2027, 1, 15), date(2027, 2, 15), True),
    ],
)
def test_common_and_deferred_expire_from_different_dates(
    payment_type: str, payment_date: date | None, on: date, expired: bool
) -> None:
    if not expired:
        terms.check_presentable(payment_type, ISSUED, payment_date, on)
        return
    with pytest.raises(AppError) as caught:
        terms.check_presentable(payment_type, ISSUED, payment_date, on)
    assert caught.value.code == "cheque_presentation_expired"


def _physical(number: str, branch: str | None, account: str | None) -> identity.Identity:
    return identity.compute(
        kind="PHYSICAL", bank_id=1, number=number, branch=branch, account_ref=account, echeq_id=None, drawer_tax_id=None
    )


def test_physical_identity_ignores_zeros_and_needs_its_account() -> None:
    a = _physical("00012345", "0123", "0004567890")
    b = _physical("12345", "123", "4567890")
    assert a.instrument_key == b.instrument_key == "P:1:123:4567890:12345"
    assert a.strategy == "PHYSICAL_BANK_DATA"
    cbu = _physical("12345", None, "0070123401000012345678")
    assert cbu.instrument_key == "P:1::70123401000012345678:12345"
    assert identity.is_complete("PHYSICAL", cbu)
    # Account number without its branch, or no account: not identifiable.
    assert _physical("12345", None, "4567890").instrument_key is None
    assert not identity.is_complete("PHYSICAL", _physical("12345", "123", None))


def test_possible_duplicates() -> None:
    mine = _physical("12345", None, None)
    assert identity.may_be_same("PHYSICAL", mine, "P:1:123:4567890:12345", None)
    complete = _physical("12345", "123", "4567890")
    assert not identity.may_be_same("PHYSICAL", complete, "P:1:123:999:12345", None)
    assert identity.may_be_same("PHYSICAL", complete, None, None)


def test_echeq_identity_has_two_levels() -> None:
    def echeq(external: str | None, account: str | None, tax: str | None) -> identity.Identity:
        return identity.compute(
            kind="ECHEQ", bank_id=1, number="1", branch=None, account_ref=account, echeq_id=external, drawer_tax_id=tax
        )

    assert echeq("ECH-1", None, "30712345678").strategy == "ECHEQ_EXTERNAL_ID"
    by_bank = echeq(None, "0070123401000012345678", "30712345678")
    assert by_bank.strategy == "ECHEQ_BANK_DATA"
    assert by_bank.instrument_key == "E:1:70123401000012345678:1:30712345678"
    # Bank and number alone never identify an ECHEQ.
    weak = echeq(None, None, "30712345678")
    assert weak.strategy is None
    assert not identity.is_complete("ECHEQ", weak)


@pytest.mark.parametrize(
    ("value", "error"), [("12-345", None), ("12a45", "invalid_cheque_data"), ("1" * 21, "invalid_cheque_data")]
)
def test_numbers_are_digits(value: str, error: str | None) -> None:
    if error is None:
        assert identity.digits(value, "cheque_number", max_length=20) == "12345"
    else:
        with pytest.raises(AppError):
            identity.digits(value, "cheque_number", max_length=20)


D = Decimal


@pytest.mark.parametrize(
    ("kind", "voided", "verification", "position", "expected"),
    [
        ("ECHEQ", False, "DECLARED", {}, "DECLARED"),
        ("ECHEQ", False, "VERIFICATION_REJECTED", {}, "VERIFICATION_REJECTED"),
        ("ECHEQ", False, "VERIFIED", {}, "RECEIVED"),
        ("PHYSICAL", False, None, {}, "RECEIVED"),
        ("PHYSICAL", False, None, {"CHEQUE_PORTFOLIO": D(10)}, "IN_PORTFOLIO"),
        # Marked for removal in a correction: still in the portfolio until the new close.
        ("PHYSICAL", True, None, {"CHEQUE_PORTFOLIO": D(10)}, "IN_PORTFOLIO"),
        ("PHYSICAL", True, None, {}, "VOIDED"),
        ("PHYSICAL", False, None, {"CHEQUES_IN_TRANSIT": D(10)}, "DEPOSITED"),
    ],
)
def test_derived_state(
    kind: str, voided: bool, verification: str | None, position: dict[str, Decimal], expected: str
) -> None:
    assert state.derive(kind=kind, voided=voided, verification_status=verification, position=position) == expected


def test_only_received_or_verified_cheques_justify() -> None:
    assert state.justifies(kind="PHYSICAL", voided=False, verification_status=None)
    assert state.justifies(kind="ECHEQ", voided=False, verification_status="VERIFIED")
    assert not state.justifies(kind="ECHEQ", voided=False, verification_status="DECLARED")
    assert not state.justifies(kind="ECHEQ", voided=False, verification_status="VERIFICATION_REJECTED")
    assert not state.justifies(kind="PHYSICAL", voided=True, verification_status=None)


def test_load_change_during_a_correction() -> None:
    assert state.load_change(being_corrected=False, in_effect=False, voided=False, justifies_now=True) is None
    assert state.load_change(being_corrected=True, in_effect=False, voided=False, justifies_now=True) == "ADDED"
    assert (
        state.load_change(being_corrected=True, in_effect=True, voided=True, justifies_now=False)
        == "MARKED_FOR_REMOVAL"
    )
    assert state.load_change(being_corrected=True, in_effect=True, voided=False, justifies_now=True) is None
