"""W2-C2 acceptance report: PASS only when every test of the criterion ran and passed (same rules as W2-A/C1).

These tests install real Windows services on a disposable GitHub Actions runner (administrator, Windows Server).
They refuse to run anywhere else. Results go to ``DISTRICOM_C2_RESULTS`` as JSON and Markdown.
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
    "C2-1": "Runtimes con versión y checksum verificados; instalación de los servicios idempotente, con cuentas virtuales",
    "C2-2": "Al arrancar, /api/v1/health/ready responde 200 a través de Caddy (certificado verificado)",
    "C2-3": "Orden de arranque por disponibilidad y dependencias; PostgreSQL no se arranca por dependencia",
    "C2-4": "Si se mata el backend, WinSW lo reinicia; después del tercer reinicio deja de intentar",
    "C2-5": "Parada ordenada: «Finished server process», sin sesiones de la app y PostgreSQL apagado limpio",
    "C2-6": "Sin el disco de backup o con otra identidad, PostgreSQL no arranca y queda el motivo; montaje tardío",
    "C2-7": "Desinstalación sin servicios ni procesos huérfanos; los datos quedan",
    "C9A-1": "Bloqueo exclusivo entre procesos reales; la segunda operación falla y nombra la primera; esperas acotadas",
    "C9A-2": "Un dueño muerto libera el bloqueo y el estado persistente (ventana, registro) queda idéntico",
    "C9A-3": "Dos procesos reales abren mantenimiento a la vez: uno solo lo consigue y el otro no toca nada",
    "C9A-4": "services start y --direct se niegan con ventana abierta, vencida o ilegible y con operación incompleta o incierta",
    "C9A-5": "Arranque con estado inseguro: PostgreSQL arriba, app abajo, fase blocked con motivo; services start da FAIL",
    "C9A-6": "Reiniciar el Stack, reiniciar PostgreSQL o vencer la ventana no sacan de blocked",
    "C9A-7": "Dos services start simultáneos: uno arranca y el otro falla al instante, sin confirmación equivocada",
    "C9A-9": "Sin interbloqueo entre services start y el supervisor",
    "C9A-10": "Sin hueco: mantenimiento rechazado mientras el supervisor decide y arranca; si entra antes, la fase es blocked",
    "C9A-11": "Procesos matados durante el arranque: sin PASS falso, sin espera sin fin, bloqueo liberado",
    "C9A-12": "PostgreSQL no se reinicia con el bloqueo ocupado, una ventana abierta o una operación incompleta o incierta",
    "C9A-13": "Un registro antiguo del supervisor nunca da PASS",
}
PENDING = {
    "REBOOT": "Arranque después de un reinicio real de Windows y sin iniciar sesión (laboratorio)",
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server 2025",
    "DISK": "Disco de backup desconectado físicamente (en CI se desconecta un disco virtual: simulación)",
    "SERIAL": "Identidad física del disco por número de serie (requiere privilegios; laboratorio)",
    "ACL": "Permisos NTFS definitivos de carpetas y secretos (C3)",
    "SHUTDOWN": "Parada ordenada durante el apagado de Windows (C10, C11 y laboratorio)",
    "C9A_REBOOT": "Reinicio real de Windows con un estado blocked (C9-A; laboratorio)",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
metrics: dict[str, Any] = {}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32" or os.environ.get("DISTRICOM_C2_ACCEPTANCE") != "1":
        skip = pytest.mark.skip(reason="installs real Windows services: only on the disposable CI runner")
        for item in items:
            item.add_marker(skip)


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
    out_dir = Path(os.environ.get("DISTRICOM_C2_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "criteria": criteria,
        "metrics": metrics,
        "pending": PENDING,
    }
    (out_dir / "w2c2-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# W2-C2: servicios nativos de Windows y W2-C9-A: coordinación de operaciones",
        "",
        f"- Plataforma: {results['platform']} · Python {results['python']}",
        "",
        "| Criterio | Descripción | Resultado |",
        "|---|---|---|",
        *(f"| {c} | {v['description']} | **{v['status']}** |" for c, v in criteria.items()),
        "",
        "## Métricas",
        "",
        *(f"- **{k}:** {v}" for k, v in metrics.items()),
        "",
        "## PENDIENTE (no reproducible en GitHub Actions; nunca cuenta como PASS)",
        "",
        *(f"- **{k}**: {v}" for k, v in PENDING.items()),
        "",
    ]
    (out_dir / "w2c2-results.md").write_text("\n".join(lines), encoding="utf-8")
