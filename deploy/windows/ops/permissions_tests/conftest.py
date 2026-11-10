"""W2-C3 acceptance report: PASS only when every test of the criterion ran and passed (same rules as C1/C2).

The tests run on a disposable GitHub Actions runner (administrator, Windows Server) after the W2-C2 setup tests,
and refuse to run anywhere else. Results go to ``DISTRICOM_C3_RESULTS`` as JSON and Markdown, with the matrix of
effective access measured under each identity.
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
    "C3-1": "Cada servicio corre con su cuenta virtual (Get-CimInstance Win32_Service), nunca LocalSystem",
    "C3-2": "Un servicio ajeno y un usuario local de prueba no pueden leer config\\ ni los datos de PostgreSQL",
    "C3-3": "El backend no puede escribir en releases\\ (ni ningún otro servicio)",
    "C3-4": "Aplicar las ACL dos veces no cambia nada; los permisos sueltos se corrigen; un enlace ajeno frena todo",
    "C3-5": "Ningún secreto ni objeto de la instalación queda accesible para Users, Authenticated Users ni Everyone",
    "C3-E": "Ejecutables: nadie fuera de Administradores puede alterarlos y el registro de integridad detecta un cambio",
    "C3-S": "Con las ACL definitivas, los cinco servicios arrancan, responden por Caddy y se detienen en orden",
}
PENDING = {
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server 2025",
    "ACCOUNTS": "Cuentas y grupo de IT reales del cliente (hoy: Administradores); cuentas de dominio desconocidas",
    "STORESYS": "Que Store Sys y su proveedor no tengan acceso, comprobado con sus cuentas reales en el laboratorio",
    "BACKUP": "Cuenta de backups y permisos de la carpeta de backups en el disco (C6)",
    "DEFENDER": "Defender u otro antivirus sobre ejecutables y datos con estas ACL",
    "REBOOT": "Arranque con estas ACL después de un reinicio real sin iniciar sesión",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
metrics: dict[str, Any] = {}
matrix: dict[str, dict[str, str]] = {}  # check → identity → "✓"/"✗" (with "!" when not the expected one)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32" or os.environ.get("DISTRICOM_C3_ACCEPTANCE") != "1":
        skip = pytest.mark.skip(reason="changes real NTFS permissions: only on the disposable CI runner")
        for item in items:
            if "permissions_tests" in str(item.path):
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
    out_dir = Path(os.environ.get("DISTRICOM_C3_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "criteria": criteria,
        "metrics": metrics,
        "matrix": matrix,
        "pending": PENDING,
    }
    (out_dir / "w2c3-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    identities = sorted({who for row in matrix.values() for who in row})
    lines = [
        "# W2-C3: cuentas de servicio y permisos NTFS",
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
        "## Acceso efectivo medido bajo cada identidad (✓ permitido, ✗ denegado, ! distinto de lo esperado)",
        "",
        "| Comprobación | " + " | ".join(identities) + " |",
        "|---|" + "---|" * len(identities),
        *(f"| {check} | " + " | ".join(row.get(w, "") for w in identities) + " |" for check, row in matrix.items()),
        "",
        "## PENDIENTE (no reproducible en GitHub Actions; nunca cuenta como PASS)",
        "",
        *(f"- **{k}**: {v}" for k, v in PENDING.items()),
        "",
    ]
    (out_dir / "w2c3-results.md").write_text("\n".join(lines), encoding="utf-8")
