"""R9-5-3: no personal data in the logs. A database error that carries a CUIT, a phone, an email and a name in its
DETAIL and in its statement parameters is logged by class, SQLSTATE and constraint only, by the app and by uvicorn;
the access log names the user by id and counts the request's queries."""

import io
import json
import logging
import logging.config
from collections.abc import Callable, Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from uvicorn.config import LOGGING_CONFIG

from app.core.logging import configure_logging
from app.db.session import get_db

CUIT = "30712345671"
PHONE = "11-5555-1234"
EMAIL = "juana.perez@example.com"
NAME = "Juana Pérez"
PII = (CUIT, PHONE, EMAIL, NAME, "Failing row", "DETAIL", "[SQL:", "[parameters:")


@pytest.fixture
def logs() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """The app's real logging setup (JSON lines, uvicorn routed through it), written to a buffer and read back."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    names = ("uvicorn", "uvicorn.error")
    uvicorn = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate) for name in names}
    # What `uvicorn` does before it imports the app: its loggers get their own plain-text handler.
    logging.config.dictConfig(LOGGING_CONFIG)
    configure_logging("INFO")
    buffer = io.StringIO()
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)

    def _read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in buffer.getvalue().splitlines() if line.startswith("{")]

    yield _read
    root.handlers = saved[0]
    root.setLevel(saved[1])
    for name, (handlers, propagate) in uvicorn.items():
        logging.getLogger(name).handlers = handlers
        logging.getLogger(name).propagate = propagate


def _handlers_of(name: str) -> set[str]:
    """The formatters of every handler a record of that logger reaches."""
    logger: logging.Logger | None = logging.getLogger(name)
    found: set[str] = set()
    while logger is not None:
        found |= {
            type(h.formatter).__name__ for h in logger.handlers if not h.__class__.__name__.startswith("LogCapture")
        }
        logger = logger.parent if logger.propagate else None
    return found


def _violation(db: Session, user_id: int) -> None:
    """A row with a CUIT, a phone, an email and a name that breaks a CHECK: PostgreSQL's DETAIL quotes the row."""
    db.execute(
        text(
            "INSERT INTO third_parties (name, name_key, tax_id, notes, created_by, updated_by)"
            " VALUES (:name, '', :cuit, :notes, :user, :user)"
        ),
        {"name": NAME, "cuit": CUIT, "notes": f"tel {PHONE} {EMAIL}", "user": user_id},
    )


def test_the_raw_error_does_carry_the_personal_data(db: Session, make_user) -> None:  # type: ignore[no-untyped-def]
    """The control: without the sanitizing, the message would leak every value."""
    user = make_user()
    with pytest.raises(IntegrityError) as raised:
        _violation(db, user.id)
    db.rollback()
    message = str(raised.value)
    assert all(value in message for value in (CUIT, PHONE, EMAIL, NAME, "Failing row"))


def test_an_unhandled_database_error_is_logged_without_personal_data(  # type: ignore[no-untyped-def]
    client: TestClient, make_user, logs
) -> None:
    from app.main import app

    user = make_user()

    def broken(db: Annotated[Session, Depends(get_db)]) -> None:
        _violation(db, user.id)

    app.add_api_route("/api/v1/_test/broken", broken)
    try:
        with TestClient(app, raise_server_exceptions=False) as raw:
            response = raw.get("/api/v1/_test/broken", headers={"X-Request-ID": "pii-test-0001"})
    finally:
        app.router.routes.pop()
    assert response.status_code == 500
    assert all(value not in response.text for value in PII)
    records = logs()
    error = next(r for r in records if r["message"] == "unhandled_error")
    assert error["request_id"] == "pii-test-0001"
    assert error["error_class"] == "sqlalchemy.exc.IntegrityError"
    assert error["db_error_class"] == "psycopg.errors.CheckViolation"
    assert error["sqlstate"] == "23514"
    assert error["constraint"] == "ck_third_parties_name_key_not_blank"
    assert error["table"] == "third_parties"
    assert "broken" in error["exc_info"]  # the frames stay, for debugging
    for line in json.dumps(records, ensure_ascii=False).split("\n"):
        assert all(value not in line for value in PII), line


def test_uvicorns_own_report_of_the_error_is_sanitized_too(db: Session, make_user, logs) -> None:  # type: ignore[no-untyped-def]
    user = make_user()
    try:
        _violation(db, user.id)
    except IntegrityError as exc:
        db.rollback()
        logging.getLogger("uvicorn.error").error("Exception in ASGI application\n", exc_info=exc)
    records = logs()
    assert [r["logger"] for r in records] == ["uvicorn.error"]
    assert _handlers_of("uvicorn.error") == {"JsonFormatter"}  # no plain-text copy anywhere else
    assert records[0]["sqlstate"] == "23514"
    assert all(value not in json.dumps(records, ensure_ascii=False) for value in PII)


def test_any_exception_is_logged_without_its_message(logs) -> None:  # type: ignore[no-untyped-def]
    try:
        try:
            raise ValueError(f"cliente {NAME} CUIT {CUIT}")
        except ValueError as inner:
            raise RuntimeError(f"falló {EMAIL}") from inner
    except RuntimeError:
        logging.getLogger("app.test").exception("failed")
    (record,) = logs()
    assert record["error_chain"] == ["builtins.RuntimeError", "builtins.ValueError"]
    assert "sqlstate" not in record
    assert all(value not in json.dumps(record, ensure_ascii=False) for value in PII)


# --- access log -----------------------------------------------------------------------------------


def test_the_access_log_names_the_user_by_id_and_counts_queries(login, logs) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    me = client.get("/api/v1/auth/me", headers={"X-Request-ID": "access-test-01"})
    anonymous = client.get("/api/v1/health")
    records = [r for r in logs() if r["message"] == "request"]
    by_path = {r["path"]: r for r in records}
    assert me.status_code == anonymous.status_code == 200
    access = by_path["/api/v1/auth/me"]
    assert access["request_id"] == "access-test-01"
    assert access["user_id"] == me.json()["id"]
    assert access["queries"] >= 1
    assert by_path["/api/v1/health"]["user_id"] is None
    assert "admin@districom.test" not in json.dumps(records)
    assert set(access) == {
        "ts",
        "level",
        "logger",
        "message",
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
        "user_id",
        "queries",
    }


def test_the_query_string_never_reaches_the_access_log(login, logs) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    client.get("/api/v1/third-parties", params={"q": NAME, "tax_id": CUIT})
    records = [r for r in logs() if r["message"] == "request"]
    assert records
    assert all(value not in json.dumps(records, ensure_ascii=False) for value in (NAME, CUIT))
