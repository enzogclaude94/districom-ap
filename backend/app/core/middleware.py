"""Request correlation, access logging, CSRF guard, no-store and the request body limit."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.body_limit import BodySizeLimit
from app.core.config import get_settings
from app.core.errors import NO_STORE, error_body
from app.core.logging import query_count_var, request_id_var

logger = logging.getLogger("app.access")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
CSRF_HEADER = "X-Requested-With"


def register_middleware(app: FastAPI) -> None:
    # Innermost of these: the guards and the request context below see its 413 like any other response.
    settings = get_settings()
    app.add_middleware(
        BodySizeLimit,
        max_bytes=settings.request_max_body_bytes,
        upload_max_bytes=settings.upload_max_size_mb * 1024 * 1024,
    )

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
        queries = [0]
        query_count_var.set(queries)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            # Every response, whatever the endpoint: JSON, CSV, errors, sessions, health (R9-5-2).
            response.headers.update(NO_STORE)
            return response
        finally:
            # Path only: query strings may carry filters with customer identifiers. The user by id, never by email.
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    "user_id": getattr(request.state, "user_id", None),
                    "queries": queries[0],
                },
            )
