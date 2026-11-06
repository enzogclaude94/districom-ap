"""W2-B: which database errors are a temporary 503 (app/core/errors.py). Fixed cases, no database needed.

The server's own codes keep their meaning (25P03 stays `database_timeout`, on any platform). A lost connection without
SQLSTATE, which Windows can show instead of 25P03, is `database_connection_lost` only when SQLAlchemy found the
connection closed and invalidated it; an error without SQLSTATE on a usable connection is not a 503.
"""

import psycopg
import pytest
from psycopg import errors
from sqlalchemy.exc import OperationalError

from app.core.errors import CONNECTION_LOST, connection_lost, database_timeout


def _wrapped(orig: Exception, *, invalidated: bool) -> OperationalError:
    return OperationalError("SELECT 1", {}, orig, connection_invalidated=invalidated)


@pytest.mark.parametrize("invalidated", [False, True])
def test_the_idle_transaction_code_keeps_its_answer(invalidated: bool) -> None:
    error = _wrapped(errors.IdleInTransactionSessionTimeout("terminating connection"), invalidated=invalidated)
    answer = database_timeout(error)
    assert answer is not None
    assert (answer.status_code, answer.code) == (503, "database_timeout")


def test_a_lost_connection_without_sqlstate_is_a_temporary_503() -> None:
    error = _wrapped(psycopg.OperationalError("consuming input failed: server closed the connection"), invalidated=True)
    assert connection_lost(error)
    answer = database_timeout(error)
    assert answer is not None
    assert (answer.status_code, answer.code, answer.message) == (503, *CONNECTION_LOST)
    assert "Revisá el resultado antes de volver a intentarla" in answer.message


def test_an_error_without_sqlstate_on_a_usable_connection_is_not_a_lost_connection() -> None:
    error = _wrapped(psycopg.OperationalError("something else went wrong"), invalidated=False)
    assert not connection_lost(error)
    assert database_timeout(error) is None


@pytest.mark.parametrize(
    "orig",
    [
        errors.UniqueViolation("duplicate key"),  # a broken rule, never a 503
        errors.AdminShutdown("terminating connection due to administrator command"),  # has its code: unchanged
    ],
)
def test_errors_with_their_own_code_are_unchanged_even_on_an_invalidated_connection(orig: Exception) -> None:
    error = _wrapped(orig, invalidated=True)
    assert not connection_lost(error)
    assert database_timeout(error) is None
