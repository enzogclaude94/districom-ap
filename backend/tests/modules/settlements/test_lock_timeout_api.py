"""R9-5-2: a payment that waits for a lock longer than the backend's `lock_timeout` (10 s), held by another
connection, is a standard 503 that records nothing, and the next payment goes through."""

import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.modules.settlements.days import today
from tests.modules.settlements.direct_support import account_id, fund
from tests.modules.settlements.payment_support import cash, counts, pay, third_party


def test_a_payment_that_cannot_get_its_lock_is_503_and_records_nothing(as_role, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin: TestClient = as_role("admin")
    fund(admin, "main_cash", "500000", today() - timedelta(days=10))
    box = account_id(admin, "main_cash")
    perez = third_party(admin)
    before = counts(db)
    other = create_engine(get_settings().database_url, poolclass=pool.NullPool)  # another client, server defaults
    with other.begin() as conn:
        conn.execute(text("SELECT id FROM settlements.accounts WHERE id = :id FOR UPDATE"), {"id": box})
        started = time.monotonic()
        response = pay(admin, perez, [cash(box, "1000.00")])
        waited = time.monotonic() - started
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "database_busy"
    assert 9.5 < waited < 20
    db.rollback()
    assert counts(db) == before
    assert pay(admin, perez, [cash(box, "1000.00")]).status_code == 201
