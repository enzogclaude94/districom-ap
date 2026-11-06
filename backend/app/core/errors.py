"""Normalized API errors: every error response is {code, message, details, request_id}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import db_error_fields, request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Business or application error with a stable code and a user-safe message."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}


_HTTP_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "request_too_large",
}

# API responses are never stored by a browser or a proxy (R9-5-2); set by the request middleware, and here for the
# one response that is built outside it (the unhandled error).
NO_STORE = {"Cache-Control": "no-store"}

# The backend's connection limits (app/db/session.py): the statement ran too long, a lock did not come in time, or the
# server closed a transaction left idle.
_TIMEOUTS = {
    "57014": ("database_timeout", "La operación tardó demasiado y se canceló. Probá de nuevo en unos minutos."),
    "25P03": ("database_timeout", "La operación tardó demasiado y se canceló. Probá de nuevo en unos minutos."),
    "55P03": ("database_busy", "Hay otra operación en curso sobre los mismos datos. Probá de nuevo en unos segundos."),
}

# The connection to PostgreSQL was lost and the driver got no SQLSTATE: on Windows the server's own error (for example
# 25P03 above) can be lost when it closes the socket (W2-B, ADR 016). The operation may or may not have been recorded
# (a COMMIT under way), so the message asks to check before trying again. Nothing is retried automatically.
CONNECTION_LOST = (
    "database_connection_lost",
    "Se perdió la conexión con la base de datos y no se pudo confirmar si la operación quedó registrada. "
    "Revisá el resultado antes de volver a intentarla.",
)


def database_timeout(exc: DBAPIError) -> AppError | None:
    """The 503 for a database error caused by the connection limits, None for any other: a timeout is never a broken
    rule, so code that turns database errors into rule errors asks this first. A connection the driver saw closed
    without a SQLSTATE (on Windows the server's FATAL can be lost with the socket, W2-B) is also a 503, with a message
    that asks to check the result before retrying; any other error without SQLSTATE is left alone."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate in _TIMEOUTS:
        code, message = _TIMEOUTS[sqlstate]
        return AppError(503, code, message)
    if connection_lost(exc):
        return AppError(503, *CONNECTION_LOST)
    return None


def connection_lost(exc: DBAPIError) -> bool:
    """The connection itself is gone, not a statement that failed: no SQLSTATE from the server, and SQLAlchemy found the
    connection closed and invalidated it (its dialect checks the driver's connection state and disconnect messages).
    An error without SQLSTATE on a connection that is still usable is not this."""
    return getattr(exc.orig, "sqlstate", None) is None and exc.connection_invalidated


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Strip submitted values ("input") so passwords or PII never echo back.
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=422, content=error_body("validation_error", "Los datos enviados no son válidos.", details)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)))

    @app.exception_handler(DBAPIError)
    async def _database_error(_: Request, exc: DBAPIError) -> JSONResponse:
        timeout = database_timeout(exc)
        if timeout is None:
            raise exc  # any other database error stays an unhandled 500, as before
        if timeout.code == CONNECTION_LOST[0]:
            # Only the driver's error class: its message may carry the server address.
            logger.warning("database_connection_lost", extra={"db_error_class": type(exc.orig).__name__})
        else:
            logger.warning("database_timeout", extra=db_error_fields(exc))
        return JSONResponse(status_code=503, content=error_body(timeout.code, timeout.message))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return JSONResponse(
            status_code=500, content=error_body("internal_error", "Ocurrió un error inesperado."), headers=NO_STORE
        )
