"""Request body limit, in real bytes (R9-5-2).

A declared `Content-Length` over the limit is answered with 413 before a byte of the body is read. Without it
(chunked) or when it understates the body, the bytes are counted as they arrive and reading stops at the first chunk
that crosses the limit, so a body is never buffered past it. File uploads (`multipart/form-data`) get their own,
larger limit (`UPLOAD_MAX_SIZE_MB`).
"""

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body

MESSAGE = "El contenido enviado supera el tamaño máximo permitido."


class RequestTooLarge(HTTPException):
    """Raised while the body is read. An HTTPException, so FastAPI's body parsing lets it through (it turns any other
    error into a 400) and the error handlers answer it in the standard format."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=MESSAGE)


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int, upload_max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        is_upload = headers.get("content-type", "").lower().startswith("multipart/form-data")
        limit = self.upload_max_bytes if is_upload else self.max_bytes
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await _reject(scope, receive, send)
            return

        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestTooLarge
            return message

        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except RequestTooLarge:
            # Read outside the error handlers (a middleware, say): answer here, unless a response already began.
            if started:
                raise
            await _reject(scope, receive, send)


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse(status_code=413, content=error_body("request_too_large", MESSAGE))(scope, receive, send)
