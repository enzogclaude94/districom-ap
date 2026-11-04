"""W2-A criteria report.

Every test that gives evidence for an acceptance criterion carries ``@pytest.mark.criterion("A-n")``.
At the end of the session the results are written to ``DISTRICOM_POC_RESULTS`` (default
``test-results/``) as JSON and Markdown:

* ``PASS``: every test of the criterion ran and passed.
* ``FAIL``: at least one of its tests failed or errored.
* ``NOT RUN``: none of its tests ran in this session.

What GitHub Actions cannot reproduce well enough is listed separately as ``PENDIENTE`` and is
never counted as PASS (decision of Enzo, 2026-11-04).
"""

import json
import os
import platform
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

CRITERIA: dict[str, str] = {
    "A-1": "Archivado efectivo hasta un LSN conocido (archive_timeout y pg_switch_wal), sin exigir pg_wal vacío",
    "A-2": "Archivado idempotente: mismo segmento dos veces",
    "A-3": "Contenido distinto: error, nunca sobrescribe",
    "A-4": "Archivo en uso: reintento o falla limpia, nunca un archivo parcial",
    "A-5": "Repositorio no disponible: PostgreSQL conserva el WAL y se pone al día",
    "A-6": "Backup base verificado con pg_verifybackup",
    "A-7": "Backup alterado: pg_verifybackup lo detecta",
    "A-8": "PITR por tiempo",
    "A-9": "PITR por punto con nombre",
    "A-10": "Restauración hasta el final del WAL archivado",
    "A-11": "Corte abrupto: confirmadas, archivadas y recuperables",
    "A-12": "Huecos de WAL y timelines (.history)",
    "A-13": "Cifrado de la copia externa contra carpeta local",
    "A-14": "Clave equivocada: error claro",
    "A-15": "Retención con dependencias y timelines",
    "A-16": "UTF-8, ICU es-AR y orden de textos",
    "A-17": "Tiempos medidos (sin límite como criterio)",
}

# Verified outside pytest: the CI job runs ruff, mypy and the tests (A-18); the PR diff and the
# four existing jobs show the Linux design is untouched (A-19).
OUTSIDE_PYTEST = {
    "A-18": "Calidad: ruff, ruff format, mypy estricto y tests del paquete (pasos del job de CI)",
    "A-19": "Diseño Linux intacto: los 4 jobs existentes en verde y ningún archivo fuera del alcance (revisión del PR)",
}

PENDING: dict[str, str] = {
    "A-4": "Con Microsoft Defender activo y la indexación de Windows sobre el repositorio real (laboratorio Windows 11 Pro)",
    "A-5": "Con un disco de backup desconectado físicamente (laboratorio; nunca en el servidor con Store Sys)",
    "A-11": "Con un corte de energía real del equipo (laboratorio; nunca en el servidor con Store Sys)",
    "A-13": "Contra el bucket real, con bloqueo de objetos (inmutabilidad) y retención validados: requiere la cuenta del proveedor externo, no autorizada",
    "PG-SERVICE": "PostgreSQL instalado como servicio de Windows con su cuenta de servicio y permisos NTFS sobre el repositorio",
    "WIN11": "Todo lo anterior sobre Windows 11 Pro: los runners de GitHub Actions son Windows Server",
    "REAL-SCHEMA": "Validación con el esquema real de DistriCom (migraciones) y el generador de datos ficticios: etapa posterior",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
_metrics: dict[str, Any] = {}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "criterion(id): W2-A acceptance criterion")


@pytest.fixture(scope="session")
def metrics() -> dict[str, Any]:
    """Measurements recorded into the results file (A-11 counts, A-17 timings...)."""
    return _metrics


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    item_criteria: list[str] = getattr(report, "criteria", [])
    for criterion in item_criteria:
        if report.when == "call" or report.outcome != "passed":
            _outcomes[criterion].append(report.outcome)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    setattr(report, "criteria", [m.args[0] for m in item.iter_markers("criterion")])  # noqa: B010
    return report


def _status(criterion: str) -> str:
    outcomes = _outcomes.get(criterion)
    if not outcomes:
        return "NOT RUN"
    if any(o != "passed" for o in outcomes):
        return "FAIL"
    return "PASS"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    out_dir = Path(os.environ.get("DISTRICOM_POC_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "postgresql": os.environ.get("DISTRICOM_PG_VERSION", "unknown"),
        "criteria": criteria,
        "outside_pytest": OUTSIDE_PYTEST,
        "pending": PENDING,
        "metrics": _metrics,
    }
    (out_dir / "w2a-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# W2-A: resultados de la prueba de concepto",
        "",
        f"- Plataforma: {results['platform']}",
        f"- PostgreSQL: {results['postgresql']} · Python {results['python']}",
        "",
        "| Criterio | Descripción | Resultado |",
        "|---|---|---|",
        *(f"| {c} | {v['description']} | **{v['status']}** |" for c, v in criteria.items()),
        *(f"| {c} | {d} | Ver el job de CI |" for c, d in OUTSIDE_PYTEST.items()),
        "",
        "## PENDIENTE (no reproducible en GitHub Actions; nunca cuenta como PASS)",
        "",
        *(f"- **{k}**: {v}" for k, v in PENDING.items()),
        "",
        "## Mediciones",
        "",
        "```json",
        json.dumps(_metrics, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    (out_dir / "w2a-results.md").write_text("\n".join(lines), encoding="utf-8")
