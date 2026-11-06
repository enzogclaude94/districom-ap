"""Diagnostics for findings of B-1: they record what Windows does, to report it exactly (no criterion of their own).

Idle transaction: on Linux the server's FATAL (SQLSTATE 25P03) reaches the client and the backend answers 503
``database_timeout``. On Windows the FATAL can be lost with the socket; this records what the client gets there and
that the backend still answers a 503 (``database_connection_lost`` when no SQLSTATE arrives).
"""

import time
from collections.abc import Generator
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import database_timeout
from app.db.session import get_db, get_engine


def test_idle_transaction_closed_by_the_server(engine: Engine, metrics: dict[str, Any]) -> None:
    sessions = cast(Generator[Session], get_db())
    session = next(sessions)
    session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '100ms'"))
    time.sleep(0.5)
    error: DBAPIError | None = None
    try:
        session.execute(text("SELECT 1"))
    except DBAPIError as exc:
        error = exc
    finally:
        sessions.close()
    assert error is not None, "the server did not close the idle transaction"
    message = str(error.orig).strip().splitlines()
    answer = database_timeout(error)
    metrics["diagnóstico B-1: transacción inactiva cerrada por el servidor"] = {
        "excepción": f"{type(error.orig).__module__}.{type(error.orig).__name__}",
        "sqlstate": getattr(error.orig, "sqlstate", None),
        "mensaje": message[:3],
        "connection_invalidated": error.connection_invalidated,
        "respuesta del backend": f"{answer.status_code} {answer.code}" if answer else "500 genérico",
    }
    assert answer is not None
    assert answer.status_code == 503
    with get_engine().connect() as conn:  # a broken connection is never handed out again
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
