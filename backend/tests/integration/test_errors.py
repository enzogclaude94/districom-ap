from fastapi.testclient import TestClient


def test_validation_error_is_normalized_and_does_not_echo_input(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "secret-value", "password": ""},
        headers={"X-Request-ID": "abc12345-test"},
    )
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == "validation_error"
    assert body["request_id"] == "abc12345-test"
    assert "secret-value" not in response.text


def test_unknown_route_is_normalized(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
