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
    "user_module_roles",
    "financial_institutions",
    "vehicles",
    "third_parties",
    "outlets",
}


def _schemas(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_schema_names())
    finally:
        engine.dispose()


SETTLEMENTS_TABLES = {
    "accounts",
    "financial_operations",
    "ledger_entries",
    "settlements",
    "cash_count_lines",
    "cash_additions",
    "settlement_transfers",
    "transfer_status_events",
    "expense_categories",
    "expenses",
    "settlement_closings",
    "settlement_reopenings",
    "settlement_differences",
    "difference_allocations",
    "pending_collections",
    "settlement_closing_pendings",
    "pending_collection_events",
    "pending_collection_receipts",
    "pending_collection_resolutions",
    "pending_resolution_allocations",
    "cheques",
    "cheque_events",
    "settlement_closing_cheques",
    "cheque_deposits",
    "cheque_deposit_items",
    "cheque_credits",
    "cheque_credit_items",
    "cheque_rejections",
    "cheque_returns",
    "fuel_loads",
    "settlement_closing_fuel_loads",
    "payments",
    "payment_items",
    "outlet_responsibilities",
    "outlet_remittances",
    "settlement_closing_remittances",
    "employee_compensation_terms",
}

# Added by 0024 (R7-3).
REMITTANCE_TABLES = {"outlet_remittances", "settlement_closing_remittances"}
# Added by 0025 (R8-1).
EMPLOYEE_FINANCE_TABLES = {"employee_compensation_terms"}


def _settlements_tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names(schema="settlements"))
    finally:
        engine.dispose()


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
    assert "settlements" not in _schemas(url)

    command.upgrade(cfg, "head")
    assert _tables(url) >= EXPECTED_TABLES
    assert "settlements" in _schemas(url)
    assert _settlements_tables(url) == SETTLEMENTS_TABLES

    command.downgrade(cfg, "0004")
    assert _settlements_tables(url) == set()
    command.upgrade(cfg, "head")
    assert _settlements_tables(url) == SETTLEMENTS_TABLES

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


def test_settlements_system_accounts_seeded(migrated_db: None) -> None:
    url = alembic_config().get_main_option("sqlalchemy.url")
    assert url
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, kind, allow_negative FROM settlements.accounts ORDER BY id")).all()
    engine.dispose()
    assert [tuple(row) for row in rows] == [
        ("main_cash", "CASH_BOX", False),
        ("change_cash", "CASH_BOX", False),
        ("bank", "BANK", False),
        ("opening_balance", "OPENING_BALANCE", True),
        ("adjustment", "ADJUSTMENT", True),
        ("delivery_collections", "DELIVERY_COLLECTIONS", True),
        ("expense", "EXPENSE", True),
        ("settlement_difference", "SETTLEMENT_DIFFERENCE", True),
        ("pending_collections", "PENDING_COLLECTIONS", False),
        ("uncollectible", "UNCOLLECTIBLE", False),
        ("cheque_portfolio", "CHEQUE_PORTFOLIO", False),
        ("cheques_in_transit", "CHEQUES_IN_TRANSIT", False),
        ("merchandise_payments", "MERCHANDISE_PAYMENTS", False),
        ("third_party_refunds", "THIRD_PARTY_REFUNDS", False),
        ("other_payments", "OTHER_PAYMENTS", False),
        ("outlet_collections", "OUTLET_COLLECTIONS", True),
    ]


def test_module_roles_migration_keeps_viewers_in_commercial() -> None:
    """Stage 0 viewers could read Inteligencia Comercial; after 0003 they keep it as an explicit role."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url
    command.downgrade(cfg, "0002")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (email, full_name, role) VALUES "
                    "('v@districom.test', 'V', 'viewer'), ('a@districom.test', 'A', 'admin')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT u.email, r.module_key, r.role FROM user_module_roles r JOIN users u ON u.id = r.user_id")
            ).all()
        assert [tuple(row) for row in rows] == [("v@districom.test", "commercial", "viewer")]
    finally:
        with engine.begin() as conn:
            # Not TRUNCATE ... CASCADE: since 0024 it would reach the seeded accounts through the bocas.
            conn.execute(text("DELETE FROM user_module_roles"))
            conn.execute(text("DELETE FROM users"))
            conn.execute(text("ALTER TABLE users ALTER COLUMN id RESTART"))
        engine.dispose()


def test_account_name_key_backfill() -> None:
    """0006 derives name_key from the existing names: case, accents and spacing do not count."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url
    command.downgrade(cfg, "0005")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO settlements.accounts (name, kind, allow_negative)"
                    " VALUES ('  José   ÁLVAREZ ', 'OWNER_EQUITY', true)"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            keys = conn.execute(text("SELECT name_key FROM settlements.accounts ORDER BY id")).scalars().all()
        assert keys == [
            "caja principal",
            "caja de cambio",
            "banco",
            "saldo inicial",
            "ajustes",
            "jose alvarez",
            "cobranzas de reparto",
            "gastos operativos",
            "diferencias de rendicion",
            "pendientes de cobro",
            "incobrables",
            "cartera de cheques",
            "cheques depositados",
            "pagos por mercaderia",
            "devoluciones a terceros",
            "otros pagos",
            "cobranzas de boca",
        ]
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM settlements.accounts WHERE kind = 'OWNER_EQUITY'"))
        engine.dispose()


def test_platform_vehicles_migration_goes_down_and_up() -> None:
    """R5-1 [22]: 0014 only adds `public.vehicles` (and its guards); going back to 0013 removes exactly that."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    assert "vehicles" in _tables(url)
    command.downgrade(cfg, "0013")
    assert "vehicles" not in _tables(url)
    assert _tables(url) >= EXPECTED_TABLES - {"vehicles", "third_parties", "outlets"}
    command.upgrade(cfg, "head")
    assert "vehicles" in _tables(url)


def test_third_parties_and_payments_migrations_go_down_and_up() -> None:
    """R6-1: 0019 only adds the payments, their technical accounts and PAYMENT; 0018 only `public.third_parties`
    and the CUIT rule. Going back removes exactly that; coming up again restores it."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0018")
    assert (
        _settlements_tables(url)
        == SETTLEMENTS_TABLES
        - {"payments", "payment_items", "outlet_responsibilities"}
        - REMITTANCE_TABLES
        - EMPLOYEE_FINANCE_TABLES
    )
    assert "third_parties" in _tables(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            kinds = conn.execute(text("SELECT kind FROM settlements.accounts")).scalars().all()
        assert {"MERCHANDISE_PAYMENTS", "THIRD_PARTY_REFUNDS", "OTHER_PAYMENTS"}.isdisjoint(kinds)
        command.downgrade(cfg, "0017")
        assert "third_parties" not in _tables(url)
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT to_regprocedure('public.cuit_is_valid(text)') IS NOT NULL")).scalar()
        assert exists is False
    finally:
        engine.dispose()
        command.upgrade(cfg, "head")
    assert _settlements_tables(url) == SETTLEMENTS_TABLES
    assert "third_parties" in _tables(url)


def test_outlets_and_responsibilities_migrations_go_down_and_up() -> None:
    """R7-1: 0022 only adds `settlements.outlet_responsibilities` (and its guard); 0021 only `public.outlets`
    (and its guards). Going back removes exactly that; coming up again restores it."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0021")
    assert (
        _settlements_tables(url)
        == SETTLEMENTS_TABLES - {"outlet_responsibilities"} - REMITTANCE_TABLES - EMPLOYEE_FINANCE_TABLES
    )
    assert "outlets" in _tables(url)
    engine = create_engine(url)
    try:
        command.downgrade(cfg, "0020")
        assert "outlets" not in _tables(url)
        assert _tables(url) >= EXPECTED_TABLES - {"outlets"}
        assert (
            _settlements_tables(url)
            == SETTLEMENTS_TABLES - {"outlet_responsibilities"} - REMITTANCE_TABLES - EMPLOYEE_FINANCE_TABLES
        )
        with engine.connect() as conn:
            leftovers = conn.execute(
                text(
                    "SELECT proname FROM pg_proc WHERE proname IN "
                    "('check_outlet_update', 'forbid_outlet_delete', 'check_outlet_responsibility')"
                )
            ).all()
        assert leftovers == []
    finally:
        engine.dispose()
        command.upgrade(cfg, "head")
    assert _settlements_tables(url) == SETTLEMENTS_TABLES
    assert "outlets" in _tables(url)
