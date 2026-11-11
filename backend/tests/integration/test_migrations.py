import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

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
# F1.1b (0033): the import framework, kept apart so the tests that go below 0033 keep comparing EXPECTED_TABLES.
IMPORT_TABLES = {"import_files", "import_jobs", "import_rows", "data_quality_issues"}


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
    "employee_movements",
    "employee_loans",
    "employee_loan_installments",
    *(
        "employee_payrolls",
        "employee_payroll_lines",
        "employee_payroll_choices",
        "employee_payroll_closings",
        "employee_payroll_closing_lines",
        "employee_payroll_applications",
        "employee_payroll_reopenings",
        "employee_payouts",
        "employee_payout_items",
    ),
    "expected_amount_events",
}

# Added by 0030 (R9-1).
EXPECTED_AMOUNT_TABLES = {"expected_amount_events"}
# Added by 0024 (R7-3).
REMITTANCE_TABLES = {"outlet_remittances", "settlement_closing_remittances"}
# Added by 0025 (R8-1), 0026 (R8-2), 0027 (R8-3), 0028 (R8-4) and 0029 (R8-5).
EMPLOYEE_FINANCE_TABLES = {
    "employee_compensation_terms",
    "employee_movements",
    "employee_loans",
    "employee_loan_installments",
    "employee_payrolls",
    "employee_payroll_lines",
    "employee_payroll_choices",
    "employee_payroll_closings",
    "employee_payroll_closing_lines",
    "employee_payroll_applications",
    "employee_payroll_reopenings",
    "employee_payouts",
    "employee_payout_items",
}


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
        ("employee_consumptions", "EMPLOYEE_CONSUMPTIONS", True),
        ("employee_adjustments", "EMPLOYEE_ADJUSTMENTS", True),
        ("payroll_expense", "PAYROLL_EXPENSE", True),
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
            "consumos de empleados",
            "ajustes a empleados",
            "sueldos y bonos internos",
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
        - EXPECTED_AMOUNT_TABLES
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
        == SETTLEMENTS_TABLES
        - {"outlet_responsibilities"}
        - REMITTANCE_TABLES
        - EMPLOYEE_FINANCE_TABLES
        - EXPECTED_AMOUNT_TABLES
    )
    assert "outlets" in _tables(url)
    engine = create_engine(url)
    try:
        command.downgrade(cfg, "0020")
        assert "outlets" not in _tables(url)
        assert _tables(url) >= EXPECTED_TABLES - {"outlets"}
        assert (
            _settlements_tables(url)
            == SETTLEMENTS_TABLES
            - {"outlet_responsibilities"}
            - REMITTANCE_TABLES
            - EMPLOYEE_FINANCE_TABLES
            - EXPECTED_AMOUNT_TABLES
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


def test_faster_ledger_guards_migration_goes_down_and_up() -> None:
    """R9-4: 0031 only rewrites two guard functions and adds one index. Going back drops the index and restores the
    functions exactly as 0030 had them; coming up again restores 0031's."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url
    guards = text(
        "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'settlements' AND p.proname IN ('check_account_not_negative', 'assert_outlet_advances')"
    )
    index = text("SELECT to_regclass('settlements.ix_ledger_entries_account_id_include_amount') IS NOT NULL")

    engine = create_engine(url)
    try:
        # 0030 as it is built on its own, not as 0031's downgrade leaves it.
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0029")
        command.upgrade(cfg, "0030")
        with engine.connect() as conn:
            before = dict(conn.execute(guards).all())
            assert conn.execute(index).scalar() is False
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            faster = dict(conn.execute(guards).all())
            assert conn.execute(index).scalar() is True
        assert set(faster) == set(before) == {"check_account_not_negative", "assert_outlet_advances"}
        assert all(faster[name] != before[name] for name in before)
        assert "IS NOT DISTINCT FROM" not in faster["assert_outlet_advances"]

        command.downgrade(cfg, "0030")
        with engine.connect() as conn:
            assert dict(conn.execute(guards).all()) == before
            assert conn.execute(index).scalar() is False
    finally:
        engine.dispose()
        command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert dict(conn.execute(guards).all()) == faster
    finally:
        engine.dispose()


def test_import_framework_migration_goes_down_and_up_and_never_drops_history() -> None:
    """F1.1b: 0033 only adds the import framework's tables. Empty, going back to 0032 removes exactly them; with a
    job recorded, the downgrade is refused (the import history is never dropped)."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    assert _tables(url) >= EXPECTED_TABLES | IMPORT_TABLES
    command.downgrade(cfg, "0032")
    assert _tables(url).isdisjoint(IMPORT_TABLES)
    assert _tables(url) >= EXPECTED_TABLES
    command.upgrade(cfg, "head")
    assert _tables(url) >= IMPORT_TABLES

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            user_id = conn.execute(
                text("INSERT INTO users (email, full_name, role) VALUES ('m@x.test', 'M', 'admin') RETURNING id")
            ).scalar_one()
            file_id = conn.execute(
                text(
                    "INSERT INTO import_files (original_filename, storage_key, size_bytes, sha256, uploaded_by) "
                    "VALUES ('a.csv', 'k', 1, 'h', :u) RETURNING id"
                ),
                {"u": user_id},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO import_jobs (status, file_id, created_by) VALUES ('processing', :f, :u)"),
                {"f": file_id, "u": user_id},
            )
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(cfg, "0032")
        assert _tables(url) >= IMPORT_TABLES
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM import_jobs"))
            conn.execute(text("DELETE FROM import_files"))
            conn.execute(text("DELETE FROM users WHERE email = 'm@x.test'"))
        engine.dispose()
        command.upgrade(cfg, "head")


# F1.2 (0034 platform, 0035 commercial).
MASTER_TABLES = {"customers", "external_entity_mappings"}
COMMERCIAL_TABLES = {
    "zones",
    "zone_visit_days",
    "zone_classification_rules",
    "price_lists",
    "product_categories",
    "source_category_mappings",
    "document_types",
    "no_purchase_reason_categories",
    "no_purchase_reason_mappings",
    "products",
    "product_families",
    "product_prices",
    # F1.3 (0036, 0037)
    "customer_zone_assignments",
    "customer_salesperson_assignments",
    "documents",
    "sale_lines",
    # F1.4 (0038)
    "no_purchase_events",
}


def _commercial_tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names(schema="commercial"))
    finally:
        engine.dispose()


def test_store_sys_fact_migrations_go_down_and_up_and_refuse_to_drop_data() -> None:
    """F1.3: 0036 creates the assignment history, 0037 documents and sale lines. Empty they go down and up; with a
    document, the downgrade is refused and nothing is dropped."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0035")
    assert _commercial_tables(url).isdisjoint({"documents", "sale_lines", "customer_zone_assignments"})
    command.upgrade(cfg, "head")
    assert _commercial_tables(url) == COMMERCIAL_TABLES

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO commercial.document_types (source_name, source_name_key, document_class, sign, "
                    "commercial_universe) VALUES ('FACTURA A', 'FACTURA A', 'INVOICE', 1, true)"
                )
            )
            conn.execute(text("INSERT INTO users (email, full_name, role) VALUES ('m@districom.test', 'M', 'admin')"))
            conn.execute(
                text(
                    "INSERT INTO import_files (original_filename, storage_key, size_bytes, sha256, uploaded_by) "
                    "SELECT 'f.xls', 'k', 1, repeat('a', 64), id FROM users"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO import_jobs (status, file_id, created_by) "
                    "SELECT 'completed', f.id, f.uploaded_by FROM import_files f"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO commercial.documents (document_type_id, source_type_name, point_of_sale, number, "
                    "document_key, business_date, net_amount, total_amount, discount_amount, import_job_id, "
                    "source_row_hash) SELECT t.id, 'FACTURA A', '0003', '00000001', 'FACTURA A|0003|00000001', "
                    "'2026-10-23', 100, 121, 0, j.id, '\\x00' FROM commercial.document_types t, import_jobs j"
                )
            )
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(cfg, "0036")
        assert _commercial_tables(url) == COMMERCIAL_TABLES
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE commercial.documents DISABLE TRIGGER trg_documents_no_delete"))
            conn.execute(text("DELETE FROM commercial.documents"))
            conn.execute(text("ALTER TABLE commercial.documents ENABLE TRIGGER trg_documents_no_delete"))
            conn.execute(text("ALTER TABLE commercial.document_types DISABLE TRIGGER trg_document_types_no_delete"))
            conn.execute(text("DELETE FROM commercial.document_types"))
            conn.execute(text("ALTER TABLE commercial.document_types ENABLE TRIGGER trg_document_types_no_delete"))
            conn.execute(text("TRUNCATE import_jobs, import_files, users CASCADE"))
        engine.dispose()


def test_masters_migrations_go_down_and_up_and_refuse_to_drop_data() -> None:
    """F1.2: 0035 creates the `commercial` schema and exactly its twelve tables; 0034 the platform's customers and
    mappings. Empty they go down and up; with a customer or a zone, the downgrade is refused."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    assert _tables(url) >= MASTER_TABLES
    assert _commercial_tables(url) == COMMERCIAL_TABLES
    command.downgrade(cfg, "0034")
    assert "commercial" not in _schemas(url)
    command.downgrade(cfg, "0033")
    assert _tables(url).isdisjoint(MASTER_TABLES)
    assert _tables(url) >= EXPECTED_TABLES | IMPORT_TABLES
    command.upgrade(cfg, "head")
    assert _commercial_tables(url) == COMMERCIAL_TABLES

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO commercial.zones (source_name, name_key, display_name, zone_type, requires_visit, "
                    "configuration_origin) VALUES ('RUTA 03 - MISA', 'RUTA 03 - MISA', 'MISA', 'UNCLASSIFIED', false, "
                    "'NONE')"
                )
            )
            conn.execute(text("INSERT INTO customers (display_name, display_name_key) VALUES ('X', 'X')"))
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(cfg, "0034")
        assert _commercial_tables(url) == COMMERCIAL_TABLES
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE commercial.zones DISABLE TRIGGER trg_zones_no_delete"))
            conn.execute(text("DELETE FROM commercial.zones"))
            conn.execute(text("ALTER TABLE commercial.zones ENABLE TRIGGER trg_zones_no_delete"))
        command.downgrade(cfg, "0034")
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(cfg, "0033")
        assert _tables(url) >= MASTER_TABLES
    finally:
        command.upgrade(cfg, "0034")
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE customers"))
        engine.dispose()
        command.upgrade(cfg, "head")


def test_no_purchase_events_migration_goes_down_and_up_and_refuses_to_drop_data() -> None:
    """F1.4: 0038 creates the AXUM no-purchase events. Empty it goes down and up; an event is never deleted and,
    with one, the downgrade is refused and nothing is dropped."""
    cfg = alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    assert url

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0037")
    assert "no_purchase_events" not in _commercial_tables(url)
    command.upgrade(cfg, "head")
    assert _commercial_tables(url) == COMMERCIAL_TABLES

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (email, full_name, role) VALUES ('m@districom.test', 'M', 'admin')"))
            conn.execute(
                text(
                    "INSERT INTO import_files (original_filename, storage_key, size_bytes, sha256, uploaded_by) "
                    "SELECT 'f.xlsx', 'k', 1, repeat('a', 64), id FROM users"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO import_jobs (status, file_id, created_by) "
                    "SELECT 'completed', f.id, f.uploaded_by FROM import_files f"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO commercial.no_purchase_reason_mappings (source_system_id, reason_source, reason_key) "
                    "SELECT id, 'CERRADO', 'CERRADO' FROM source_systems WHERE code = 'AXUM'"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO commercial.no_purchase_events (business_date, source_customer_code, "
                    "source_salesperson, reason_source, reason_mapping_id, uploaded_at_source, import_job_id, "
                    "occurrence, status) "
                    "SELECT '2026-10-20', '101', '01', 'CERRADO', r.id, '2026-10-20 12:30', j.id, 1, 'ACTIVE' "
                    "FROM commercial.no_purchase_reason_mappings r, import_jobs j"
                )
            )
        with pytest.raises(DBAPIError, match="never deleted"), engine.begin() as conn:
            conn.execute(text("DELETE FROM commercial.no_purchase_events"))
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(cfg, "0037")
        assert _commercial_tables(url) == COMMERCIAL_TABLES
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE commercial.no_purchase_events DISABLE TRIGGER trg_no_purchase_events_no_delete")
            )
            conn.execute(text("DELETE FROM commercial.no_purchase_events"))
            conn.execute(
                text("ALTER TABLE commercial.no_purchase_events ENABLE TRIGGER trg_no_purchase_events_no_delete")
            )
            conn.execute(text("DELETE FROM commercial.no_purchase_reason_mappings"))
            conn.execute(text("TRUNCATE import_jobs, import_files, users CASCADE"))
        engine.dispose()
