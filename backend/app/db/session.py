from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import query_count_var


@lru_cache
def get_engine() -> Engine:
    """The backend's engine. Every connection it opens starts with the backend's limits, as connection options: no
    `ALTER ROLE` or `ALTER DATABASE`, so migrations, backups, restores and dumps keep the server's defaults."""
    settings = get_settings()
    options = " ".join(
        (
            f"-c statement_timeout={settings.db_statement_timeout_ms}",
            f"-c lock_timeout={settings.db_lock_timeout_ms}",
            f"-c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout_ms}",
        )
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"options": options})
    event.listen(engine, "before_cursor_execute", _count_query)
    return engine


def _count_query(*_: Any) -> None:
    count = query_count_var.get()
    if count is not None:
        count[0] += 1


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with _session_factory()() as session:
        yield session
