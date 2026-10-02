from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db.session import get_engine


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}
    assert response.headers["X-Request-ID"]


def test_health_reports_db_down(client: TestClient) -> None:
    from app.main import app

    dead = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    app.dependency_overrides[get_engine] = lambda: dead
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}
