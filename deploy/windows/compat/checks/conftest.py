"""W2-B: environment, fixtures and the PASS/FAIL report per criterion.

These checks import the backend as it is. They never change it: a failure here is an incompatibility to report
(Enzo, 2026-11-06), not something to patch in a module.
"""

import json
import os
import platform
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "backend"

COMPAT_URL = os.environ.get(
    "DISTRICOM_COMPAT_DATABASE_URL", "postgresql+psycopg://districom:districom@127.0.0.1:5432/districom_compat"
)
os.environ["DATABASE_URL"] = COMPAT_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ARGON2_PROFILE", "test")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("CORS_ORIGINS", "")

from alembic import command  # noqa: E402  (the settings above must exist before the app is imported)
from alembic.config import Config  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

CRITERIA: dict[str, str] = {
    "B-4": "Orden de textos en español (ICU es-AR) en los listados que ordena el backend",
    "B-5": "Zona horaria America/Argentina/Buenos_Aires y fechas de negocio, con tzdata explícito",
    "B-6": "Arranque del backend con uvicorn, health, login con Argon2, configuración insegura y parada ordenada",
    "B-10": "Componentes reutilizables: verify.sql, logs seguros (O7) y Caddyfile",
}

_outcomes: dict[str, list[tuple[str, str]]] = {key: [] for key in CRITERIA}
_metrics: dict[str, Any] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        for criterion in getattr(report, "criteria", ()):
            _outcomes[criterion].append((report.nodeid, report.outcome))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    report.criteria = [mark.args[0] for mark in item.iter_markers("criterion")]


def pytest_sessionfinish(session: pytest.Session) -> None:
    target = os.environ.get("DISTRICOM_COMPAT_RESULTS")
    if not target:
        return
    results = {}
    for key in CRITERIA:
        outcomes = _outcomes[key]
        if not outcomes:
            results[key] = "SIN EVIDENCIA"
        elif all(o == "passed" for _, o in outcomes):
            results[key] = "PASS"
        else:
            results[key] = "FAIL"
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "postgresql": os.environ.get("DISTRICOM_PG_VERSION", ""),
        "criteria": results,
        "tests": {key: _outcomes[key] for key in CRITERIA},
        "metrics": _metrics,
    }
    (folder / "w2b-compat.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "## Chequeos de compatibilidad (B-4, B-5, B-6, B-10)",
        "",
        f"- Plataforma: {data['platform']} · Python {data['python']} · {data['postgresql']}",
        "",
        "| Criterio | Descripción | Resultado |",
        "|---|---|---|",
        *(f"| {k} | {CRITERIA[k]} | **{results[k]}** |" for k in CRITERIA),
        "",
        "```json",
        json.dumps(_metrics, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    (folder / "w2b-compat.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def metrics() -> dict[str, Any]:
    return _metrics


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """The compat database, migrated to head once. A disposable database, never a production one."""
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", COMPAT_URL)
    command.upgrade(config, "head")
    engine = create_engine(COMPAT_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back at the end: the checks leave nothing behind."""
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
