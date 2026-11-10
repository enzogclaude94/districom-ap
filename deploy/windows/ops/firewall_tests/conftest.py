"""W2-C4 acceptance report: PASS only when every test of the criterion ran and passed (same rules as C1 to C3).

The tests run on a disposable GitHub Actions runner (administrator, Windows Server) after the W2-C2 setup tests,
and refuse to run anywhere else. Results go to ``DISTRICOM_C4_RESULTS`` as JSON and Markdown, with the rules as
Windows reports them. What needs a real network (another PC of the LAN, Tailscale, Remote Desktop) is PENDIENTE.
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
    "C4-1": "Las reglas DistriCom-* existen con su alcance exacto (puerto, origen, programa, acción, perfil)",
    "C4-2": "Aplicar dos veces no duplica ni reescribe reglas; una regla alterada o sobrante se corrige",
    "C4-3": "5432, 8000 y 3000 escuchan solo en 127.0.0.1, y cada uno lo abre el programa que nombra su regla",
    "C4-4": "Aplicar y quitar las reglas de DistriCom no toca ninguna regla ajena (Store Sys ficticio, Escritorio remoto)",
    "C4-R": "Reversible: quitar deja el firewall como estaba y volver a aplicar da las mismas reglas",
    "C4-S": "Con las reglas aplicadas, los servicios arrancan y responden por Caddy; ninguna regla bloquea 3389",
}
PENDING = {
    "LAN": "Acceso real desde otra PC de la LAN a 443/80 y rechazo a 5432/8000/3000 (en CI no hay otra máquina)",
    "TAILSCALE": "Acceso por Tailscale (100.64.0.0/10) con la política real",
    "RDP": "Escritorio remoto real del grupo IT desde la LAN y Tailscale, con la sesión abierta y reversión a mano",
    "RDP_SCOPE": "Si las reglas propias de Escritorio remoto de Windows se restringen: decisión de IT, no de DistriCom",
    "STORESYS": "Puertos y reglas reales de Store Sys y su proveedor (en CI: reglas ficticias)",
    "PROFILE": "Perfil de red real del equipo (Privado/Público/Dominio) y el router sin reenvío de puertos",
    "WIN11": "Windows 11 Pro: los runners de GitHub Actions son Windows Server 2025",
}

_outcomes: dict[str, list[str]] = defaultdict(list)
metrics: dict[str, Any] = {}
rules_table: list[dict[str, str]] = []  # the DistriCom rules as Windows reports them, for the report


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32" or os.environ.get("DISTRICOM_C4_ACCEPTANCE") != "1":
        skip = pytest.mark.skip(reason="changes real firewall rules: only on the disposable CI runner")
        for item in items:
            if "firewall_tests" in str(item.path):
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
    out_dir = Path(os.environ.get("DISTRICOM_C4_RESULTS", "test-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    criteria = {c: {"description": d, "status": _status(c)} for c, d in CRITERIA.items()}
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "criteria": criteria,
        "metrics": metrics,
        "rules": rules_table,
        "pending": PENDING,
    }
    (out_dir / "w2c4-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    columns = ["name", "action", "protocol", "local_ports", "remote_addresses", "program", "profile"]
    lines = [
        "# W2-C4: Firewall de Windows",
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
        "## Reglas DistriCom como las informa Windows",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |" for row in rules_table),
        "",
        "## PENDIENTE (no reproducible en GitHub Actions; nunca cuenta como PASS)",
        "",
        *(f"- **{k}**: {v}" for k, v in PENDING.items()),
        "",
    ]
    (out_dir / "w2c4-results.md").write_text("\n".join(lines), encoding="utf-8")
