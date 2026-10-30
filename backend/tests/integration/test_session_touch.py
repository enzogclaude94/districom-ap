"""R9-5-1: a session's `last_seen_at` is written at most once per `session_touch_seconds` (60), only after every check
passed with the persisted value, with a conditional UPDATE that one request wins and that never revives a session."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_engine
from app.platform.auth import service
from app.platform.auth.deps import SESSION_COOKIE
from app.platform.auth.security import hash_token

IDLE = timedelta(hours=get_settings().session_idle_hours)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def session_client(login) -> TestClient:  # type: ignore[no-untyped-def]
    return login("admin")  # type: ignore[no-any-return]


def _run(statement: str, **params: Any) -> None:
    """Another client of the database (its own engine, so the counters below do not see it) writes and commits."""
    engine = create_engine(get_settings().database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(statement), params)
    finally:
        engine.dispose()


def _change(session_id: int, change: str, **params: Any) -> None:
    _run(f"UPDATE user_sessions SET {change} WHERE id = :id", id=session_id, **params)  # noqa: S608


def _row(db: Session, client: TestClient) -> Any:
    db.rollback()  # a fresh snapshot
    return db.execute(
        text("SELECT id, last_seen_at, expires_at, revoked_at FROM user_sessions WHERE token_hash = :h"),
        {"h": hash_token(client.cookies[SESSION_COOKIE])},
    ).one()


@pytest.fixture
def clock(db: Session, session_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The app's clock, starting at the session's persisted `last_seen_at` (set by the database at login)."""
    at = Clock(_row(db, session_client).last_seen_at)
    monkeypatch.setattr(service, "_now", at)
    return at


@pytest.fixture
def writes() -> Iterator[dict[str, int]]:
    """Session UPDATEs sent, rows they changed, read-only rechecks after an UPDATE that matched nothing, and
    commits, on the app's engine."""
    counts = {"updates": 0, "rows": 0, "rechecks": 0, "commits": 0}
    engine = get_engine()

    def _after(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.startswith("UPDATE user_sessions"):
            counts["updates"] += 1
            counts["rows"] += cursor.rowcount
        elif statement.startswith("SELECT user_sessions.id \nFROM user_sessions") and "EXISTS" in statement:
            counts["rechecks"] += 1

    def _commit(conn: Any) -> None:
        counts["commits"] += 1

    event.listen(engine, "after_cursor_execute", _after)
    event.listen(engine, "commit", _commit)
    yield counts
    event.remove(engine, "after_cursor_execute", _after)
    event.remove(engine, "commit", _commit)


def test_first_request_after_the_window_touches_once(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    clock.at += timedelta(seconds=61)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    assert writes == {"updates": 1, "rows": 1, "rechecks": 0, "commits": 1}
    assert _row(db, session_client).last_seen_at == clock.at


def test_requests_within_60_seconds_do_not_write(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    persisted = clock.at
    for seconds in (0, 1, 30, 59):
        clock.at = persisted + timedelta(seconds=seconds)
        assert session_client.get("/api/v1/auth/me").status_code == 200
    assert writes == {"updates": 0, "rows": 0, "rechecks": 0, "commits": 0}
    assert _row(db, session_client).last_seen_at == persisted


def test_at_60_seconds_it_writes_and_the_window_starts_again(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    clock.at += timedelta(seconds=60)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    touched = clock.at
    clock.at += timedelta(seconds=59)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    assert writes["rows"] == 1
    clock.at += timedelta(seconds=1)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    assert writes["rows"] == 2
    assert _row(db, session_client).last_seen_at == touched + timedelta(seconds=60)


def test_idle_timeout_counts_from_the_persisted_activity(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    """Valid a moment before the idle limit (and touched there); then the limit counts from that touch, and at the
    limit the session is rejected and left as it was."""
    persisted = clock.at
    clock.at = persisted + IDLE - timedelta(seconds=1)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    touched = clock.at
    assert _row(db, session_client).last_seen_at == touched
    clock.at = touched + IDLE - timedelta(seconds=1)
    assert session_client.get("/api/v1/auth/me").status_code == 200
    persisted = _row(db, session_client).last_seen_at
    clock.at = persisted + IDLE
    assert session_client.get("/api/v1/auth/me").status_code == 401
    assert _row(db, session_client).last_seen_at == persisted


def test_a_request_inside_the_window_never_adds_session_time(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    """Up to 60 s less precision in the persisted activity, never 60 s more of session."""
    persisted = clock.at
    clock.at = persisted + timedelta(seconds=30)
    assert session_client.get("/api/v1/auth/me").status_code == 200  # not written
    clock.at = persisted + IDLE  # idle counted from the persisted value, not from the request at +30 s
    assert session_client.get("/api/v1/auth/me").status_code == 401


def test_an_idle_expired_session_is_never_revived(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    persisted = clock.at
    clock.at = persisted + IDLE + timedelta(hours=1)
    assert session_client.get("/api/v1/auth/me").status_code == 401
    assert session_client.get("/api/v1/auth/me").status_code == 401
    assert writes == {"updates": 0, "rows": 0, "rechecks": 0, "commits": 0}
    assert _row(db, session_client).last_seen_at == persisted


def test_absolute_expiry_rejects_even_a_recently_touched_session(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    row = _row(db, session_client)
    clock.at = row.expires_at - timedelta(seconds=1)
    _change(row.id, "last_seen_at = :at", at=clock.at)  # active until the very end of its absolute life
    assert session_client.get("/api/v1/auth/me").status_code == 200
    clock.at = row.expires_at
    assert session_client.get("/api/v1/auth/me").status_code == 401
    assert writes["rows"] == 0


def test_a_revoked_session_is_never_touched(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    token = session_client.cookies[SESSION_COOKIE]
    persisted = clock.at
    assert session_client.post("/api/v1/auth/logout").status_code == 204
    session_client.cookies.set(SESSION_COOKIE, token)
    clock.at = persisted + timedelta(seconds=61)
    assert session_client.get("/api/v1/auth/me").status_code == 401
    assert writes["updates"] == 1  # the logout's revocation only
    db.rollback()
    assert db.execute(text("SELECT last_seen_at FROM user_sessions")).scalar_one() == persisted


@contextmanager
def _race(write: Callable[[], None]) -> Iterator[None]:
    """`write` runs from another transaction right before the app's touch UPDATE: after the checks, before it."""
    engine = get_engine()

    def _before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.startswith("UPDATE user_sessions"):
            write()

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", _before)


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE user_sessions SET revoked_at = now() WHERE id = :id",
        "UPDATE user_sessions SET expires_at = :past WHERE id = :id",
        "UPDATE user_sessions SET last_seen_at = :stale WHERE id = :id",
        "UPDATE users SET active = false WHERE id = (SELECT user_id FROM user_sessions WHERE id = :id)",
    ],
    ids=["revoked", "absolute-expired", "idle-expired", "user-inactive"],
)
def test_a_session_invalidated_after_it_was_read_rejects_this_request(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int], change: str
) -> None:
    """Between the checks and the UPDATE another transaction revokes or expires the session, or deactivates its
    user: the conditional UPDATE matches nothing, the read-only recheck (same `now`) finds it invalid, and this very
    request is rejected. Nothing is touched or committed."""
    row = _row(db, session_client)
    clock.at += timedelta(seconds=61)
    with _race(lambda: _run(change, id=row.id, past=clock.at - timedelta(seconds=1), stale=clock.at - IDLE)):
        assert session_client.get("/api/v1/auth/me").status_code == 401
    assert writes == {"updates": 1, "rows": 0, "rechecks": 1, "commits": 0}
    assert _row(db, session_client).last_seen_at != clock.at
    assert session_client.get("/api/v1/auth/me").status_code == 401


def test_a_request_that_loses_the_touch_to_another_valid_one_stays_authenticated(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    """Another request touched the session between this one's checks and its UPDATE: the UPDATE matches nothing,
    the recheck finds the session still valid, and this request goes on without committing."""
    row = _row(db, session_client)
    clock.at += timedelta(seconds=61)
    with _race(lambda: _change(row.id, "last_seen_at = :at", at=clock.at)):
        assert session_client.get("/api/v1/auth/me").status_code == 200
    assert writes == {"updates": 1, "rows": 0, "rechecks": 1, "commits": 0}
    assert _row(db, session_client).last_seen_at == clock.at


def test_concurrent_requests_make_at_most_one_touch(
    db: Session, session_client: TestClient, clock: Clock, writes: dict[str, int]
) -> None:
    """Ten requests on the same session at once, each with its own connection: one UPDATE changes the row and
    commits; the others match nothing once it commits, and do not commit."""
    from app.db.session import get_db

    token = session_client.cookies[SESSION_COOKIE]
    clock.at += timedelta(seconds=61)
    settings = get_settings()
    barrier = threading.Barrier(10)
    results: list[bool] = []
    errors: list[BaseException] = []

    def _request() -> None:
        try:
            for session in get_db():
                barrier.wait(timeout=10)
                results.append(service.resolve_session(session, token, settings) is not None)
                session.rollback()
        except BaseException as error:  # reported below
            errors.append(error)

    threads = [threading.Thread(target=_request) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert results == [True] * 10
    assert writes["rows"] == 1
    assert writes["commits"] == 1
    # every other request that reached the UPDATE matched nothing and rechecked: the session was still valid
    assert writes["rechecks"] == writes["updates"] - 1
    assert _row(db, session_client).last_seen_at == clock.at
