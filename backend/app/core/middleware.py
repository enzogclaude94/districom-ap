"""Request correlation, access logging and CSRF guard."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.errors import error_body
from app.core.logging import request_id_var

logger = logging.getLogger("app.access")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
CSRF_HEADER = "X-Requested-With"


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Mutating API calls must carry a custom header. Browsers cannot add it
        # cross-site without a CORS preflight, which our CORS policy rejects.
        is_mutating_api_call = request.method not in _SAFE_METHODS and request.url.path.startswith("/api/")
        if is_mutating_api_call and not request.headers.get(CSRF_HEADER):
            return JSONResponse(status_code=403, content=error_body("csrf_header_missing", "Solicitud rechazada."))
        return await call_next(request)

    @app.middleware("http")
    async def _request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        # Not reset on exit: each request runs in its own context, and the
        # outermost 500 handler still needs the id after this middleware returns.
        request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            # Path only: query strings may carry filters with customer identifiers.
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
