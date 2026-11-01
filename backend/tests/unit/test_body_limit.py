"""R9-5-2: the body limit at the ASGI level, where what is read from the client can be counted."""

import asyncio
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.body_limit import BodySizeLimit

LIMIT = 1000
UPLOAD_LIMIT = 5000


async def echo(scope: Scope, receive: Receive, send: Send) -> None:
    """Reads the whole body, as FastAPI does for JSON, and answers its size."""
    body = await Request(scope, receive).body()
    await JSONResponse({"bytes": len(body)})(scope, receive, send)


class Client:
    """Feeds the body in chunks and records how many the app pulled."""

    def __init__(self, chunks: list[bytes], *, endless: bool = False) -> None:
        self.chunks = chunks
        self.endless = endless
        self.pulled = 0

    async def receive(self) -> Message:
        if self.endless:
            self.pulled += 1
            if self.pulled * len(self.chunks[0]) > 100 * LIMIT:
                raise AssertionError("buffered far past the limit")
            return {"type": "http.request", "body": self.chunks[0], "more_body": True}
        if self.pulled >= len(self.chunks):
            raise AssertionError("read past the end of the body")
        chunk = self.chunks[self.pulled]
        self.pulled += 1
        return {"type": "http.request", "body": chunk, "more_body": self.pulled < len(self.chunks)}


def call(client: Client, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/x",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    asyncio.run(BodySizeLimit(echo, max_bytes=LIMIT, upload_max_bytes=UPLOAD_LIMIT)(scope, client.receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body)


def test_a_declared_length_over_the_limit_is_refused_before_reading_a_byte() -> None:
    client = Client([b"x" * (LIMIT + 1)])
    status, body = call(client, {"content-length": str(LIMIT + 1)})
    assert status == 413
    assert body["code"] == "request_too_large"
    assert set(body) == {"code", "message", "details", "request_id"}
    assert client.pulled == 0


def test_up_to_the_limit_passes() -> None:
    status, body = call(Client([b"x" * LIMIT]), {"content-length": str(LIMIT)})
    assert (status, body) == (200, {"bytes": LIMIT})


def test_without_a_length_the_bytes_are_counted_and_reading_stops_at_the_limit() -> None:
    """Chunked: an endless body is cut at the first chunk past the limit, never buffered whole."""
    client = Client([b"x" * 100], endless=True)
    status, body = call(client, {})
    assert status == 413
    assert body["code"] == "request_too_large"
    assert client.pulled == LIMIT // 100 + 1


def test_a_length_that_understates_the_body_is_not_trusted() -> None:
    client = Client([b"x" * 600, b"x" * 600, b"x" * 600])
    status, _ = call(client, {"content-length": "10"})
    assert status == 413
    assert client.pulled == 2


def test_the_limit_is_in_bytes_not_characters() -> None:
    """600 «ñ» are 600 characters and 1200 bytes in UTF-8: over a 1000-byte limit."""
    data = ("ñ" * 600).encode()
    assert len(data) == 1200
    status, _ = call(Client([data]), {"content-type": "application/json; charset=utf-8"})
    assert status == 413


def test_uploads_use_their_own_limit() -> None:
    multipart = {"content-type": "multipart/form-data; boundary=x"}
    status, body = call(Client([b"x" * (LIMIT * 3)]), multipart)
    assert (status, body) == (200, {"bytes": LIMIT * 3})
    client = Client([b"x" * (UPLOAD_LIMIT + 1)])
    status, _ = call(client, {**multipart, "content-length": str(UPLOAD_LIMIT + 1)})
    assert status == 413
    assert client.pulled == 0
    status, _ = call(Client([b"x" * 3000, b"x" * 3000]), multipart)
    assert status == 413
