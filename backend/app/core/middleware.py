"""Request correlation, access logging, CSRF guard, no-store and the request body limit."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.body_limit import BodySizeLimit
from app.core.config import get_settings
from app.core.errors import NO_STORE, error_body
from app.core.logging import request_id_var

logger = logging.getLogger("app.access")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
CSRF_HEADER = "X-Requested-With"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _normalized_origin(value: str) -> str | None:
    """`scheme://host[:port]` in lower case without a default port, or None when it is not such an origin."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname or parts.path not in ("", "/"):
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    suffix = "" if port is None or port == _DEFAULT_PORTS[parts.scheme] else f":{port}"
    return f"{parts.scheme}://{host}{suffix}"


def _allowed_origins(request: Request, configured: Iterable[str]) -> set[str]:
    """The request's own origin (behind Caddy: the scheme from X-Forwarded-Proto, trusted only from Caddy, and the
    Host Caddy passes on) plus the configured ones."""
    allowed = {o for o in (_normalized_origin(v) for v in configured) if o}
    own = _normalized_origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
    if own:
        allowed.add(own)
    return allowed


def origin_rejection(request: Request) -> str | None:
    """Why a mutating API call is refused by its origin (S2), or None.

    A browser sends `Origin` with every POST, PUT, PATCH and DELETE: it must be the app's own origin or one of
    TRUSTED_ORIGINS / CORS_ORIGINS. Without `Origin` (a script, the CLI, the tests) the call goes on only if the
    browser does not say it is cross-site (`Sec-Fetch-Site`). This sits on top of the `X-Requested-With` header,
    not instead of it."""
    origin = request.headers.get("origin")
    if origin is None:
        if request.headers.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
            return "cross_site"
        return None
    settings = get_settings()
    normalized = _normalized_origin(origin)
    if normalized is None or normalized not in _allowed_origins(
        request, [*settings.trusted_origins, *settings.cors_origins]
    ):
        return "origin_not_allowed"
    return None


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
        if is_mutating_api_call and (reason := origin_rejection(request)):
            # The origin is not logged: only that the check failed (it may carry anything a client sends).
            logger.warning("origin_rejected", extra={"reason": reason, "path": request.url.path})
            return JSONResponse(status_code=403, content=error_body("origin_not_allowed", "Solicitud rechazada."))
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
            # Every response, whatever the endpoint: JSON, CSV, errors, sessions, health (R9-5-2).
            response.headers.update(NO_STORE)
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
