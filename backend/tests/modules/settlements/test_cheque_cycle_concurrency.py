"""The cheque cycle under concurrency, against PostgreSQL (R4-2): every race ends in a valid state, with each
cheque whole and in one place, and never a double deposit, a credit balance on a pending, or a discretionary
debit deepening an overdraft."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cash import schemas as cash_schemas
from app.modules.settlements.cash import use_cases as cash
from app.modules.settlements.cheques import collections, cycle
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service as cheques
from app.platform.auth.models import User
from tests.modules.settlements.test_cheques_concurrency import _bank, _in_session, _race

T = business_today()


@pytest.fixture
def new_session(db: Session) -> Iterator[Callable[[], Session]]:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    sessions: list[Session] = []

    def _new() -> Session:
        session = factory()
        sessions.append(session)
        return session

    yield _new
    for session in sessions:
        session.close()


ISSUED = T - timedelta(days=12)

_numbers = iter(range(50000, 90000))


def _entry(db: Session, admin: User, amount: str = "100000") -> int:
    number = str(next(_numbers))
    body = cs.AdministrativeEntryIn(
        client_request_id=uuid4(),
        kind="PHYSICAL",
        payment_type="COMMON",
        amount=Decimal(amount),
        issuer_bank_id=_bank(db),
        branch_code="123",
        account_ref=f"88{number}",
        cheque_number=number,
        issue_date=ISSUED,
        drawer_name="Distribuidora Sur SA",
        delivered_by_name="Almacén Don José",
        physical_receipt_confirmed=True,
        business_date=T - timedelta(days=10),
    )
    return cheques.opening_balance(db, admin, body).id


def _bank_account(db: Session) -> int:
    return db.execute(text("SELECT id FROM settlements.accounts WHERE code = 'bank'")).scalar_one()  # type: ignore[no-any-return]


def _deposit_body(db: Session, ids: list[int], request: UUID | None = None) -> cs.DepositIn:
    return cs.DepositIn(
        client_request_id=request or uuid4(),
        bank_account_id=_bank_account(db),
        cheque_ids=ids,
        business_date=T - timedelta(days=8),
    )


def _lock_cheques(holder: Session, ids: list[int]) -> None:
    holder.execute(text("SELECT 1 FROM settlements.cheques WHERE id = ANY(:ids) FOR UPDATE"), {"ids": ids})


def _check_whole(db: Session) -> None:
    db.rollback()
    positions = db.execute(
        text(
            "SELECT e.cheque_id, a.kind, sum(e.amount) FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.cheque_id IS NOT NULL "
            "GROUP BY e.cheque_id, a.kind HAVING sum(e.amount) <> 0"
        )
    ).all()
    amounts = dict(db.execute(text("SELECT id, amount FROM settlements.cheques")).all())
    held = [cid for cid, _, _ in positions]
    assert len(held) == len(set(held)), positions  # one place at most
    assert all(total == amounts[cid] for cid, _, total in positions)


def test_two_slips_with_the_same_cheque_deposit_it_once(
    db: Session, new_session: Callable[[], Session], actor: Callable[[str], User]
) -> None:
    admin, operator = actor("admin"), actor("operator")
    cid = _entry(db, admin)
    holder = new_session()
    _lock_cheques(holder, [cid])

    def _deposit(session: Session, user: User) -> object:
        return cycle.deposit(session, user, _deposit_body(session, [cid]))

    results = _race(holder, [_in_session(new_session, operator, _deposit) for _ in range(2)])
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "cheque_not_in_portfolio"
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM settlements.cheque_deposit_items")).scalar_one() == 1
    _check_whole(db)


def test_a_retried_slip_racing_itself_is_registered_once(
    db: Session, new_session: Callable[[], Session], actor: Callable[[str], User]
) -> None:
    admin, operator = actor("admin"), actor("operator")
    cid = _entry(db, admin)
    request = uuid4()
    holder = new_session()
    _lock_cheques(holder, [cid])

    def _deposit(session: Session, user: User) -> object:
        return cycle.deposit(session, user, _deposit_body(session, [cid], request))

    results = _race(holder, [_in_session(new_session, operator, _deposit) for _ in range(2)])
    assert not [r for r in results if isinstance(r, Exception)], results
    created = sorted(r.created for r in results)  # type: ignore[union-attr]
    assert created == [False, True]
    assert len({r.id for r in results}) == 1  # type: ignore[union-attr]
    _check_whole(db)


def test_a_rejection_racing_the_reversal_of_its_crediting_ends_valid(
    db: Session,
    new_session: Callable[[], Session],
    actor: Callable[[str], User],
    driver: Callable[..., int],
) -> None:
    admin, operator = actor("admin"), actor("operator")
    ana = driver("Ana")
    cid = _entry(db, admin)
    cycle.deposit(db, operator, _deposit_body(db, [cid]))
    credit_id = cycle.credit(
        db,
        operator,
        cs.CreditIn(
            client_request_id=uuid4(),
            bank_account_id=_bank_account(db),
            cheque_ids=[cid],
            business_date=T - timedelta(days=6),
        ),
    ).id
    holder = new_session()
    _lock_cheques(holder, [cid])

    def _reject(session: Session, user: User) -> object:
        body = cs.RejectionIn(
            client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=ana
        )
        return cycle.reject(session, user, cid, body)

    def _reverse_credit(session: Session, user: User) -> object:
        return cycle.reverse_credit(
            session, user, credit_id, cs.CycleReversalIn(client_request_id=uuid4(), reason="Error")
        )

    results = _race(
        holder, [_in_session(new_session, operator, _reject), _in_session(new_session, admin, _reverse_credit)]
    )
    db.rollback()
    stage = db.execute(text("SELECT stage FROM settlements.cheque_rejections")).scalar_one()
    credit_reversed = db.execute(
        text("SELECT reversal_operation_id IS NOT NULL FROM settlements.cheque_credits WHERE id = :id"),
        {"id": credit_id},
    ).scalar_one()
    if credit_reversed:
        # The reversal won: the cheque went back to "depositados" and was rejected from there.
        assert stage == "IN_TRANSIT"
        assert not [r for r in results if isinstance(r, Exception)], results
    else:
        # The rejection won: the crediting has a later document in effect and is not reversed.
        assert stage == "AFTER_CREDIT"
        assert isinstance(results[1], AppError)
        assert results[1].code == "cheque_has_activity"
    bank = db.execute(
        text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"),
        {"a": _bank_account(db)},
    ).scalar_one()
    assert bank == 0  # credited and rejected, or neither
    _check_whole(db)


def test_two_echeqs_verified_at_once_never_exceed_the_balance(
    db: Session, new_session: Callable[[], Session], actor: Callable[[str], User], driver: Callable[..., int]
) -> None:
    admin, operator = actor("admin"), actor("operator")
    cid = _entry(db, admin)
    cycle.deposit(db, operator, _deposit_body(db, [cid]))
    pending_id = db.execute(
        text("SELECT id FROM settlements.pending_collections WHERE origin_cheque_rejection_id = :r"),
        {
            "r": cycle.reject(
                db,
                operator,
                cid,
                cs.RejectionIn(
                    client_request_id=uuid4(),
                    reason="Sin fondos",
                    business_date=T,
                    responsible_employee_id=driver("Ana"),
                ),
            ).id
        },
    ).scalar_one()
    echeqs = []
    for external in ("ECH-A", "ECH-B"):
        number = str(next(_numbers))
        body = cs.ChequeReceiptIn(
            client_request_id=uuid4(),
            kind="ECHEQ",
            payment_type="COMMON",
            amount=Decimal(60000),
            issuer_bank_id=_bank(db),
            account_ref=f"99{number}",
            cheque_number=number,
            echeq_external_id=external,
            issue_date=T,
            drawer_name="Cliente SA",
            drawer_tax_id="30712345678",
            delivered_by_name="Almacén Don José",
            business_date=T,
        )
        echeqs.append(collections.receive(db, operator, pending_id, body).id)
    holder = new_session()
    holder.execute(text("SELECT 1 FROM settlements.pending_collections WHERE id = :p FOR UPDATE"), {"p": pending_id})

    def _verify(echeq_id: int) -> Callable[[Session, User], object]:
        body = cs.PendingEcheqVerificationIn(status="VERIFIED", business_date=T)
        return lambda session, user: collections.decide_echeq(session, user, pending_id, echeq_id, body) or "ok"

    results = _race(holder, [_in_session(new_session, operator, _verify(e)) for e in echeqs])
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "pending_amount_exceeds_balance"
    db.rollback()
    collected = db.execute(
        text(
            "SELECT coalesce(sum(amount), 0) FROM settlements.pending_collection_receipts "
            "WHERE pending_id = :p AND reversal_operation_id IS NULL"
        ),
        {"p": pending_id},
    ).scalar_one()
    assert collected == Decimal("60000.00")
    _check_whole(db)


def test_a_withdrawal_racing_a_rejection_never_deepens_the_overdraft(
    db: Session,
    new_session: Callable[[], Session],
    actor: Callable[[str], User],
    driver: Callable[..., int],
) -> None:
    admin, operator = actor("admin"), actor("operator")
    ana = driver("Ana")
    cid = _entry(db, admin)
    cycle.deposit(db, operator, _deposit_body(db, [cid]))
    cycle.credit(
        db,
        operator,
        cs.CreditIn(
            client_request_id=uuid4(),
            bank_account_id=_bank_account(db),
            cheque_ids=[cid],
            business_date=T - timedelta(days=6),
        ),
    )
    bank = _bank_account(db)
    holder = new_session()
    holder.execute(text("SELECT 1 FROM settlements.accounts WHERE id = :a FOR UPDATE"), {"a": bank})

    def _withdraw(session: Session, user: User) -> object:
        cash_id = session.execute(text("SELECT id FROM settlements.accounts WHERE code = 'main_cash'")).scalar_one()
        body = cash_schemas.BankWithdrawalIn(
            client_request_id=uuid4(),
            business_date=T,
            bank_account_id=bank,
            cash_account_id=cash_id,
            amount=Decimal(100000),
        )
        return cash.bank_withdrawal(session, user, body)

    def _reject(session: Session, user: User) -> object:
        body = cs.RejectionIn(
            client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=ana
        )
        return cycle.reject(session, user, cid, body)

    results = _race(
        holder, [_in_session(new_session, operator, _withdraw), _in_session(new_session, operator, _reject)]
    )
    assert not isinstance(results[1], Exception), results  # the rejection is never refused for lack of funds
    db.rollback()
    balance = db.execute(
        text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": bank}
    ).scalar_one()
    if isinstance(results[0], Exception):
        # The rejection went first: the withdrawal would have deepened the overdraft.
        assert isinstance(results[0], AppError)
        assert results[0].code == "insufficient_funds"
        assert balance == 0
    else:
        # The withdrawal went first, with the money there; then the rejection overdrew the bank.
        assert balance == Decimal("-100000.00")
    _check_whole(db)
