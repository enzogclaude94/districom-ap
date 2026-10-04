"""Single writer of the ledger (technical adjustment 2, ARCHITECTURE.md §4.6).

`app/modules/settlements/ledger/service.py` is the only application code that may write
`financial_operations` / `ledger_entries`. This test reads the source of `app/` and fails when:
- a file outside the ledger package imports the ledger models (only `app/db/models.py` may, to
  register them for Alembic);
- a file other than the service builds or writes those rows (constructor call, insert/update/delete);
- a file other than the models and the service mentions those table names (raw SQL).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"
LEDGER = APP / "modules" / "settlements" / "ledger"
SERVICE = LEDGER / "service.py"
MODELS_MODULE = "app.modules.settlements.ledger.models"
WRITTEN_MODELS = {"FinancialOperation", "LedgerEntry"}
TABLE_NAMES = ("financial_operations", "ledger_entries")
WRITE_FUNCTIONS = {"insert", "update", "delete"}


def _sources() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(), str(path))) for path in sorted(APP.rglob("*.py"))]


def _imports_models(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == MODELS_MODULE:
                return True
            if node.module == "app.modules.settlements.ledger" and any(a.name == "models" for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == MODELS_MODULE for a in node.names):
            return True
    return False


def _writes_ledger(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in WRITTEN_MODELS:
            return True
        if name in WRITE_FUNCTIONS and any(isinstance(a, ast.Name) and a.id in WRITTEN_MODELS for a in node.args):
            return True
    return False


def _mentions_tables(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and any(t in node.value for t in TABLE_NAMES)
        for node in ast.walk(tree)
    )


def test_only_the_ledger_package_imports_the_ledger_models() -> None:
    offenders = [
        path.relative_to(APP)
        for path, tree in _sources()
        if _imports_models(tree) and LEDGER not in path.parents and path != APP / "db" / "models.py"
    ]
    assert offenders == [], f"Use app.modules.settlements.ledger.service instead: {offenders}"


def test_only_the_service_writes_the_ledger() -> None:
    offenders = [path.relative_to(APP) for path, tree in _sources() if _writes_ledger(tree) and path != SERVICE]
    assert offenders == [], f"Publish operations through ledger.service: {offenders}"


def test_no_raw_sql_on_ledger_tables() -> None:
    offenders = [
        path.relative_to(APP)
        for path, tree in _sources()
        if _mentions_tables(tree) and path not in {LEDGER / "models.py", SERVICE}
    ]
    assert offenders == [], f"Raw SQL on the ledger tables is not allowed: {offenders}"


def test_the_guard_detects_violations() -> None:
    """The checks above must actually catch the patterns they forbid."""
    assert _imports_models(ast.parse("from app.modules.settlements.ledger.models import LedgerEntry"))
    assert _imports_models(ast.parse("from app.modules.settlements.ledger import models"))
    assert _writes_ledger(ast.parse("db.add(LedgerEntry(account_id=1, amount=1))"))
    assert _writes_ledger(ast.parse("db.execute(insert(FinancialOperation).values())"))
    assert _writes_ledger(ast.parse("db.execute(sa.delete(LedgerEntry))"))
    assert _mentions_tables(ast.parse("db.execute(text('INSERT INTO settlements.ledger_entries VALUES (1)'))"))
    assert not _writes_ledger(ast.parse("db.scalars(select(LedgerEntry))"))
