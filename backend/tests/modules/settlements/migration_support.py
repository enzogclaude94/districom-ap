"""Version-aware setup for the tests of migrations older than 0030 (Enzo's R9-1 adjustment before the merge).

Those tests build their data through today's API, at head, and then go below 0030. At head every expected amount gets
its event (R9-1), which 0030 rightly refuses to drop: `expected_amount_events` is the history of the expected amount
and a downgrade never destroys it. A test of R5-R7 must not carry R9 data below R9, so before going down it turns
its data into what the same data was before 0030: settlements loaded by hand, without events («Manual · anterior a
integración Store Sys»). Only for those tests; a database with Store Sys data is never turned back.
"""

from sqlalchemy import create_engine, text

from tests.conftest import alembic_config

TABLES = ("settlements.settlements", "settlements.settlement_closings", "settlements.expected_amount_events")


def as_before_r9() -> None:
    url = alembic_config().get_main_option("sqlalchemy.url")
    assert url
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            store_sys = conn.execute(
                text(
                    "SELECT count(*) FROM settlements.settlements WHERE expected_source <> 'MANUAL' "
                    "OR (type = 'DELIVERY' AND expected_amount IS NULL)"
                )
            ).scalar_one()
            store_sys += conn.execute(
                text("SELECT count(*) FROM settlements.expected_amount_events WHERE source <> 'MANUAL'")
            ).scalar_one()
            assert store_sys == 0, "R9 data that 0029 cannot hold: this test must not create it"
        # Each step commits on its own: a table with pending trigger events cannot have its triggers switched.
        with engine.begin() as conn:
            for table in TABLES:
                conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE settlements.settlement_closings SET expected_amount = NULL, expected_source = NULL, "
                    "expected_event_id = NULL, expected_reference = NULL, expected_revision = NULL"
                )
            )
            conn.execute(text("UPDATE settlements.settlements SET expected_event_id = NULL"))
            conn.execute(text("DELETE FROM settlements.expected_amount_events"))
        with engine.begin() as conn:
            for table in TABLES:
                conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    finally:
        engine.dispose()
