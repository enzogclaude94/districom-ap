"""The Rendiciones tests resolve the business day when they use it, never at import.

A module constant like `T = business_today()` freezes the day of collection: a run crossing midnight in
the business timezone then fails falsely. These tests prove the helper is lazy and keep the debt from
coming back (docs/TESTING.md, "Fecha de negocio en tests").
"""

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.core import clock
from tests.modules.settlements import days

SETTLEMENTS_TESTS = Path(__file__).resolve().parents[1] / "modules" / "settlements"
CLOCK_CALLS = {"business_today", "date.today", "datetime.now", "datetime.today", "datetime.utcnow"}


class _FrozenDatetime(datetime):
    """`datetime` whose `now()` is whatever instant the test sets."""

    instant = datetime(2026, 3, 10, 2, 59, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return cls.instant if tz is None else cls.instant.astimezone(tz)


def test_the_business_day_is_resolved_at_use_time_across_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    # 02:59 UTC is 23:59 of March 9 in Buenos Aires (UTC-3); two minutes later it is already March 10 there.
    monkeypatch.setattr(clock, "datetime", _FrozenDatetime)
    monkeypatch.setattr(_FrozenDatetime, "instant", datetime(2026, 3, 10, 2, 59, tzinfo=UTC))
    assert days.today() == date(2026, 3, 9)
    assert days.yesterday() == date(2026, 3, 8)
    monkeypatch.setattr(_FrozenDatetime, "instant", datetime(2026, 3, 10, 3, 1, tzinfo=UTC))
    assert days.today() == date(2026, 3, 10)
    assert days.yesterday() == date(2026, 3, 9)
    assert days.days_ago(12) == date(2026, 2, 26)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _calls(nodes: list[ast.AST]) -> set[str]:
    """Names called inside `nodes`, without entering lambdas (evaluated later)."""
    found: set[str] = set()
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            found.add(_dotted(node.func))
        stack.extend(ast.iter_child_nodes(node))
    return found


def _eager(statements: list[ast.stmt]) -> list[ast.AST]:
    """What runs when the module (or class body) is imported: everything except function bodies."""
    eager: list[ast.AST] = []
    for statement in statements:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            eager += statement.decorator_list + statement.args.defaults
            eager += [d for d in statement.args.kw_defaults if d is not None]
        elif isinstance(statement, ast.ClassDef):
            eager += statement.decorator_list + statement.bases
            eager += _eager(statement.body)
        else:
            eager.append(statement)
    return eager


def _import_time_clock_reads(tree: ast.Module) -> set[str]:
    clocky = set(CLOCK_CALLS)
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "tests.modules.settlements.days":
            clocky |= {alias.asname or alias.name for alias in statement.names}
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
    # A local helper that reads the clock reads it whenever it is called: follow it to a fixpoint.
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            if name not in clocky and _calls(list(function.body)) & clocky:
                clocky.add(name)
                changed = True
    return _calls(_eager(tree.body)) & clocky


def test_the_guard_flags_an_import_time_business_day() -> None:
    eager = ast.parse(
        "from tests.modules.settlements.days import today\n"
        "def issued_on():\n    return today()\n"
        "ISSUED = issued_on()\n"
        "def pay(on=today()): ...\n"
    )
    assert _import_time_clock_reads(eager) == {"issued_on", "today"}
    lazy = ast.parse(
        "from tests.modules.settlements.days import today\n"
        "def pay(on=None):\n    return on or today()\n"
        "later = lambda: today()\n"
    )
    assert _import_time_clock_reads(lazy) == set()


@pytest.mark.parametrize("path", sorted(SETTLEMENTS_TESTS.glob("*.py")), ids=lambda p: p.name)
def test_settlements_tests_do_not_read_the_business_day_at_import(path: Path) -> None:
    reads = _import_time_clock_reads(ast.parse(path.read_text(encoding="utf-8")))
    assert not reads, (
        f"{path.name} reads the business day at import time via {sorted(reads)}: use days.today() at use time"
    )
