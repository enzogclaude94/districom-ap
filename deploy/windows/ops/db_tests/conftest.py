"""W2-C9-B acceptance on a real PostgreSQL (17 in CI, the official Windows binaries): the write block, its journal,
``resume``, the roles of the new model and the migrations. Same report rules as W2-A/C1: PASS only when every
test of the criterion ran and passed.

Each module makes its own disposable cluster with the real ``database.init`` (roles, ``pg_hba.conf``, the
application database owned by ``districom_ops``) and runs it with ``pg_ctl``. They need ``DISTRICOM_PG_BIN``
(the folder with ``initdb``, ``pg_ctl`` and ``psql``) and are skipped without it. Results go to
``DISTRICOM_C9B_RESULTS`` as JSON and Markdown.
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
    "C9-1": "Con sesiones abiertas de todos los roles (uno por PUBLIC y uno de prueba), después del bloqueo ninguna escribe y ninguna conexión nueva entra",
    "C9-2": "Una transacción larga termina bien antes de cortar, o el bloqueo vence y revierte sin cortarla",
    "C9-3": "La reversión deja todo como estaba, comparado con el estado anterior",
    "C9-4": "Proceso matado en cada paso 1 a 6 y en cada estado: bloqueo libre, diario y marca como estaban, arranque bloqueado, resume termina tras verificar",
    "C9-5": "Ninguna sesión queda en la base, verificado en pg_database y pg_stat_activity",
    "C9-6": "Con la marca en false se reinicia PostgreSQL: sigue en false y el restablecimiento por postgres con districom_ops funciona",
    "C9-7": "Con la conexión operativa rota (contraseña mala o rol sin permiso) nada se habilita y queda la alerta",
    "C9-8": "La ventana vencida, un reinicio del Stack o una espera terminada no habilitan escrituras",
    "C9-9": "Repetir la reversión o un resume sobre otro resume interrumpido no cambia nada y termina",
    "C9-H1": "El rol de la app no cambia ALLOW_CONNECTIONS, no entra a postgres ni template1, no asume districom_ops; districom_ops sí cambia la marca",
    "C9-M": "Con el nuevo modelo: upgrade head desde vacío, downgrade base y upgrade head; la app es dueña de lo que crea",
    "C9-M-SUITE": "Con el nuevo modelo: la suite completa del backend pasa contra ese clúster",
    "C9-R": "Restablecimiento automático solo desde closed_complete con evidencia verificada; cualquier otra combinación queda bloqueada",
    "C9-X7": "Un resume matado en cada paso se reconcilia contra el diario y pg_database; la marca vuelve a false antes del resultado",
}
PENDING = {
    "LAN": "Ensayo con la app real cargada por usuarios en la LAN (laboratorio)",
    "REBOOT": "Reinicio real de Windows a mitad de un cierre (laboratorio)",
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server",
    "CLOSE": "Cierre completo real: no se valida antes de C10 y C11 (aquí solo diarios y evidencias ficticios)",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
metrics: dict[str, Any] = {}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not os.environ.get("DISTRICOM_PG_BIN"):
        skip = pytest.mark.skip(reason="needs a real PostgreSQL: set DISTRICOM_PG_BIN")
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
    out_dir = Path(os.environ.get("DISTRICOM_C9B_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "postgresql": metrics.get("PostgreSQL"),
        "criteria": criteria,
        "metrics": metrics,
        "pending": PENDING,
    }
    (out_dir / "w2c9b-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# W2-C9-B: bloqueo reversible de escrituras (PostgreSQL real)",
        "",
        f"- Plataforma: {results['platform']} · Python {results['python']} · {results['postgresql']}",
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
    (out_dir / "w2c9b-results.md").write_text("\n".join(lines), encoding="utf-8")
