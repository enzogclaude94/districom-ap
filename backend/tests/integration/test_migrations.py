from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "users",
    "user_sessions",
    "app_settings",
    "audit_events",
    "source_systems",
    "employees",
    "employee_functions",
}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_migrations_upgrade_downgrade_upgrade_from_scratch() -> None:
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.downgrade(cfg, "base")
    assert _tables(url) == set()

    command.upgrade(cfg, "head")
    assert _tables(url) >= EXPECTED_TABLES

    command.downgrade(cfg, "base")
    assert _tables(url) == set()

    command.upgrade(cfg, "head")
    assert _tables(url) >= EXPECTED_TABLES


def test_single_migration_head() -> None:
    assert len(ScriptDirectory.from_config(alembic_config()).get_heads()) == 1


def test_source_systems_seeded(migrated_db: None) -> None:
    url = alembic_config().get_main_option("sqlalchemy.url")
    assert url
    engine = create_engine(url)
    with engine.connect() as conn:
        codes = set(conn.execute(text("SELECT code FROM source_systems")).scalars())
    engine.dispose()
    assert codes == {"AXUM", "STORE_SYS", "MANUAL", "DEMO"}
