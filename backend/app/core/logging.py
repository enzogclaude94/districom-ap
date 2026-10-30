"""Structured JSON logging with request correlation, and no personal data in errors (R9-5-3).

An exception is logged by its class and its stack frames only, never by its message: a database error's message
carries PostgreSQL's DETAIL («Failing row contains (…)») and SQLAlchemy's statement parameters, that is, row values
such as a CUIT, a phone, an email or a name, and any other exception may quote an input. A database error adds what
identifies it without values: SQLSTATE, constraint, schema, table and column.
"""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Statements the request sent to the database: a one-item list the request middleware sets and the engine increments.
query_count_var: ContextVar[list[int] | None] = ContextVar("query_count", default=None)

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info and record.exc_info[1] is not None:
            payload.update(exception_fields(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def exception_fields(exc_info: ExcInfo) -> dict[str, Any]:
    """`error_class`, the chain of classes, the database fields when there is a database error in the chain, and the
    stack frames without any exception message."""
    chain: list[BaseException] = []
    exc: BaseException | None = exc_info[1]
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    fields: dict[str, Any] = {
        "error_class": _name(chain[0]),
        "error_chain": [_name(e) for e in chain],
        "exc_info": "".join(
            f"{_name(e)} (mensaje omitido)\n" + "".join(traceback.format_tb(e.__traceback__)) for e in reversed(chain)
        ),
    }
    for e in chain:
        fields.update(db_error_fields(e))
    return fields


def db_error_fields(exc: BaseException) -> dict[str, Any]:
    """What identifies a database error without its values; empty for anything else. Works on SQLAlchemy's wrapper
    (through `.orig`) and on the driver's error."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate is None:
        return {}
    diag = getattr(orig, "diag", None)
    fields = {"sqlstate": sqlstate, "db_error_class": _name(orig)}
    for key in ("constraint_name", "schema_name", "table_name", "column_name"):
        value = getattr(diag, key, None)
        if value:
            fields[key.removesuffix("_name") if key != "constraint_name" else "constraint"] = value
    return fields


def _name(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
    # uvicorn logs an unhandled error once more ("Exception in ASGI application"), with its own plain handler: its
    # records go through this formatter too, so they never carry the exception's message either.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
