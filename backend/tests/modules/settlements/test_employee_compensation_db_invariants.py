"""Salary terms in the database itself (R8-1, migration 0025): whatever writes, the rules hold.

A term is born open for an active employee, never overlaps another one of the same employee, ends once (day,
who, when, reason) and nothing else ever changes; nothing is deleted. Going below 0025 with terms is refused.
"""

from collections.abc import Callable, Iterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config

INSERT = (
    "INSERT INTO settlements.employee_compensation_terms (employee_id, valid_from, valid_to, base_salary, "
    "created_by, ended_by, ended_at, end_reason) VALUES (:e, :f, :t, :s, :u, :eb, :ea, :r) RETURNING id"
)
END = (
    "UPDATE settlements.employee_compensation_terms SET valid_to = :t, ended_by = :u, ended_at = now(), "
    "end_reason = 'Aumento' WHERE id = :id"
)


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


@pytest.fixture
def author(make_user) -> int:  # type: ignore[no-untyped-def]
    return int(make_user(email="rrhh@districom.test", role="viewer").id)


def _insert(
    db: Session, employee_id: int, author: int, valid_from: str, salary: str = "800000", **extra: object
) -> int:
    params = {"e": employee_id, "f": valid_from, "t": None, "s": salary, "u": author, "eb": None, "ea": None, "r": None}
    row = db.execute(text(INSERT), params | extra).scalar_one()
    db.commit()
    return int(row)


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _refused_insert(db: Session, employee_id: int, author: int, valid_from: str, **extra: object) -> str:
    params = {"e": employee_id, "f": valid_from, "t": None, "s": "1", "u": author, "eb": None, "ea": None, "r": None}
    return _refused(db, INSERT, **(params | extra))


def test_salary_not_negative_and_zero_allowed(
    db: Session, ana: int, author: int, employee: Callable[[str], int]
) -> None:
    assert _insert(db, employee("Pasante"), author, "2026-01-01", "0")
    assert "base_salary_not_negative" in _refused_insert(db, ana, author, "2026-01-01", s="-0.01")


def test_born_open_for_an_active_employee(db: Session, ana: int, author: int) -> None:
    assert "born open" in _refused_insert(
        db, ana, author, "2026-01-01", t="2026-02-01", eb=author, ea="2026-02-01T00:00:00Z", r="x"
    )
    assert "is not active" in _refused_insert(db, 999999, author, "2026-01-01")
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": ana})
    db.commit()
    assert "is not active" in _refused_insert(db, ana, author, "2026-01-01")


def test_no_overlap_per_employee(db: Session, ana: int, author: int, employee: Callable[[str], int]) -> None:
    first = _insert(db, ana, author, "2026-01-01")
    assert "ex_employee_compensation_terms_no_overlap" in _refused_insert(db, ana, author, "2026-06-01")
    # Another employee is independent.
    assert _insert(db, employee("Beto"), author, "2026-01-01")
    db.execute(text(END), {"t": "2026-05-31", "u": author, "id": first})
    db.commit()
    # Inclusive: the end day is still covered; the next day is free.
    assert "no_overlap" in _refused_insert(db, ana, author, "2026-05-31")
    assert _insert(db, ana, author, "2026-06-01")


def test_ended_once_completely_and_nothing_else_changes(db: Session, ana: int, author: int) -> None:
    term = _insert(db, ana, author, "2026-01-01")
    for column, value in (("base_salary", "1"), ("valid_from", "2025-01-01"), ("notes", "otra"), ("employee_id", 0)):
        message = _refused(
            db,
            f"UPDATE settlements.employee_compensation_terms SET {column} = :v WHERE id = :id",  # noqa: S608
            v=value,
            id=term,
        )
        assert "the only change is ending" in message, column
    assert "end_complete" in _refused(
        db, "UPDATE settlements.employee_compensation_terms SET valid_to = '2026-03-31' WHERE id = :id", id=term
    )
    assert "valid_range" in _refused(db, END, t="2025-12-31", u=author, id=term)
    db.execute(text(END), {"t": "2026-03-31", "u": author, "id": term})
    db.commit()
    assert "already ended" in _refused(db, END, t="2026-04-30", u=author, id=term)
    assert "already ended" in _refused(
        db, "UPDATE settlements.employee_compensation_terms SET base_salary = 1 WHERE id = :id", id=term
    )


def test_never_deleted(db: Session, ana: int, author: int) -> None:
    term = _insert(db, ana, author, "2026-01-01")
    _refused(db, "DELETE FROM settlements.employee_compensation_terms WHERE id = :id", id=term)
    assert db.execute(text("SELECT count(*) FROM settlements.employee_compensation_terms")).scalar_one() == 1


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_downgrade_below_0025_is_refused_while_terms_exist(
    db: Session, ana: int, author: int, back_at_head: None
) -> None:
    _insert(db, ana, author, "2026-01-01")
    db.close()
    with pytest.raises(DBAPIError, match="salary terms exist"):
        command.downgrade(alembic_config(), "0024")
    assert db.execute(text("SELECT count(*) FROM settlements.employee_compensation_terms")).scalar_one() == 1
