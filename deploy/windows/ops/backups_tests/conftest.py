"""W2-C6 acceptance report: PASS only when every test of the criterion ran and passed (same rules as C1 to C4).

The tests run on a disposable GitHub Actions runner (administrator, Windows Server) after the W2-C2 setup tests
(runtimes, backup disk, database, services, migrations) and refuse to run anywhere else. Results go to
``DISTRICOM_C6_RESULTS`` as JSON and Markdown. A PASS here is CI evidence only: what CI cannot reproduce is listed
as PENDIENTE and never counts as PASS. No result here means that the system is fully recoverable (C7, C13).
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
    "C6-1": "Con el servicio real de PostgreSQL 17 archivando, cada segmento llega a repo1 (pg_stat_archiver sin fallas)",
    "C6-2": "Las tareas se registran con su cuenta propia (SID de tarea, sin contraseña) y su horario; registrar otra vez no cambia nada",
    "C6-3": "Corridas forzadas por el Programador de tareas terminan bien, con su cuenta, y registran su estado",
    "C6-4": "Dos tareas pesadas no se superponen; la revisión de huecos, liviana, sí corre al lado",
    "C6-5": "Sin el remoto, la base sigue escribiendo y archivando, y el atraso de repo2 queda registrado",
    "C6-6": "El dump está cifrado (otra clave no lo abre) y se restauró en una instancia descartable",
    "C6-7": "Con ventana, operación incompleta, diario de C9-B o bloqueo ocupado, la tarea no empieza y registra el motivo",
    "C6-AB": "Mantenimiento y services stop con una tarea pesada en curso: pedido persistente, espera acotada fuera del bloqueo, confirmación, sin copias a medias",
    "C6-I": "Una tarea matada a mitad de camino queda interrumpida, sin temporales, y las copias anteriores verifican",
}
PENDING = {
    "SCHEDULE": "Ejecución por horario real durante días (en CI las tareas se fuerzan con schtasks /run)",
    "REBOOT": "Reinicio y apagado reales de Windows con una tarea en curso; corte de energía (laboratorio)",
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server 2025",
    "DISK": "Disco de backup real, desconectado y lleno de verdad (en CI: disco virtual)",
    "BUCKET": "Bucket real fuera del local con bloqueo de objetos y credenciales (en CI: carpeta local e inalcanzable)",
    "DEFENDER": "Defender real con las exclusiones",
    "TIMES": "Tiempos del backup base, del dump y de la copia externa en el i3-9100",
    "KEYS": "Resguardo de las claves de los dumps y de repo2 fuera del servidor (procedimiento físico)",
    "RECOVERY": "Recuperabilidad completa (backup base más WAL, restauraciones de cada copia retenida): C7 y C13",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
metrics: dict[str, Any] = {}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32" or os.environ.get("DISTRICOM_C6_ACCEPTANCE") != "1":
        skip = pytest.mark.skip(reason="runs real scheduled tasks and services: only on the disposable CI runner")
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
    out_dir = Path(os.environ.get("DISTRICOM_C6_RESULTS", "test-results"))
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
    (out_dir / "w2c6-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# W2-C6: integración de los backups (archivado, tareas programadas, copia externa, dump cifrado)",
        "",
        f"- Plataforma: {results['platform']} · Python {results['python']}",
        "- Un PASS es evidencia de CI, no una validación física ni de recuperabilidad completa.",
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
    (out_dir / "w2c6-results.md").write_text("\n".join(lines), encoding="utf-8")
