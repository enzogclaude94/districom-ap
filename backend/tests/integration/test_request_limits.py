"""R9-5-2: the request body limit and `Cache-Control: no-store` through the whole app."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings

LIMIT = get_settings().request_max_body_bytes
LOGIN = "/api/v1/auth/login"


def _json_of(size: int) -> bytes:
    """A JSON object of exactly `size` bytes: the endpoint answers 422 once it is read, 413 if it is not."""
    head, tail = b'{"email": "', b'"}'
    return head + b"a" * (size - len(head) - len(tail)) + tail


def test_the_limit_is_one_megabyte() -> None:
    assert LIMIT == 1024 * 1024


def test_a_body_over_the_limit_is_413_in_the_standard_format(client: TestClient) -> None:
    response = client.post(
        LOGIN,
        content=_json_of(LIMIT + 1),
        headers={"Content-Type": "application/json", "X-Request-ID": "limit-test-0001"},
    )
    assert response.status_code == 413
    assert response.json() == {
        "code": "request_too_large",
        "message": "El contenido enviado supera el tamaño máximo permitido.",
        "details": None,
        "request_id": "limit-test-0001",
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_a_body_at_the_limit_is_read(client: TestClient) -> None:
    response = client.post(LOGIN, content=_json_of(LIMIT), headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_a_chunked_body_over_the_limit_is_413(client: TestClient) -> None:
    sent: list[int] = []

    def chunks() -> Iterator[bytes]:
        body = _json_of(LIMIT * 3)
        for start in range(0, len(body), 64 * 1024):
            sent.append(start)
            yield body[start : start + 64 * 1024]

    response = client.post(LOGIN, content=chunks(), headers={"Content-Type": "application/json"})
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_a_chunked_body_under_the_limit_is_read(client: TestClient) -> None:
    body = _json_of(LIMIT // 2)
    response = client.post(
        LOGIN, content=iter([body[:1000], body[1000:]]), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


# --- no-store -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/v1/health", 200),
        ("GET", "/api/v1/auth/me", 401),
        ("GET", "/api/v1/does-not-exist", 404),
        ("POST", LOGIN, 422),
    ],
)
def test_every_response_is_no_store(client: TestClient, method: str, path: str, status: int) -> None:
    response = client.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"


def test_login_me_and_logout_are_no_store(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    for response in (client.get("/api/v1/auth/me"), client.post("/api/v1/auth/logout")):
        assert response.status_code in (200, 204)
        assert response.headers["Cache-Control"] == "no-store"


def test_an_unhandled_error_is_no_store(client: TestClient) -> None:
    from app.main import app

    def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/api/v1/_test/boom", boom)
    try:
        with TestClient(app, raise_server_exceptions=False) as raw:
            response = raw.get("/api/v1/_test/boom")
    finally:
        app.router.routes.pop()
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.headers["Cache-Control"] == "no-store"
