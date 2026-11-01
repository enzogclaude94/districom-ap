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


def database_timeout(exc: DBAPIError) -> AppError | None:
    """The 503 for a database error caused by the connection limits, None for any other: a timeout is never a broken
    rule, so code that turns database errors into rule errors asks this first."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate not in _TIMEOUTS:
        return None
    code, message = _TIMEOUTS[sqlstate]
    return AppError(503, code, message)


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
        logger.warning("database_timeout", extra=db_error_fields(exc))
        return JSONResponse(status_code=503, content=error_body(timeout.code, timeout.message))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return JSONResponse(
            status_code=500, content=error_body("internal_error", "Ocurrió un error inesperado."), headers=NO_STORE
        )
