"""W2-C1 criteria report, same rules as W2-A: PASS only when every test of the criterion ran and passed.

Results go to ``DISTRICOM_OPS_RESULTS`` (default ``test-results/``) as JSON and Markdown, and the CI job prints
them. What GitHub Actions cannot reproduce is listed as PENDIENTE and never counts as PASS.
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
    "C1-1": "Estructura de carpetas idempotente y configuración que rechaza variables faltantes o inseguras sin mostrar valores",
    "C1-2": "Ventana de mantenimiento con operación dueña y registro de operaciones: el vencimiento alerta sin cerrar la ventana ni habilitar escrituras",
    "C1-3": "Logs sin secretos ni datos personales",
}
OUTSIDE_PYTEST = {"C1-4": "Calidad: ruff, ruff format y mypy estricto (pasos del job de CI)"}
PENDING = {
    "ACL": "Permisos NTFS de config\\ y state\\ para las cuentas de servicio (C3)",
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server",
    "POWER": "Corte de energía real durante una escritura del registro de operaciones (laboratorio)",
}

_outcomes: dict[str, list[str]] = defaultdict(list)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    for criterion in getattr(report, "criteria", []):
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
    return "FAIL" if any(o != "passed" for o in outcomes) else "PASS"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    out_dir = Path(os.environ.get("DISTRICOM_OPS_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "criteria": criteria,
        "outside_pytest": OUTSIDE_PYTEST,
        "pending": PENDING,
    }
    (out_dir / "w2c1-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# W2-C1: base operativa",
        "",
        f"- Plataforma: {results['platform']} · Python {results['python']}",
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
    ]
    (out_dir / "w2c1-results.md").write_text("\n".join(lines), encoding="utf-8")
