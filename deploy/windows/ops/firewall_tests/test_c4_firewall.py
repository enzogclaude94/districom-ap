"""W2-C4 acceptance: the Windows Firewall rules of DistriCom, written and read for real.

The job runs this after the W2-C2 setup tests, as administrator on a disposable runner. Rules are checked with
cmdlets called here, one rule at a time (not the bulk read ``firewall.py`` uses), and the rules that are not
DistriCom's (the runner's own, Remote Desktop's and two fictitious Store Sys rules) are compared before and after
every step. A runner has no other machine on its network, so whether a LAN PC really reaches 443 and not 8000 is
PENDIENTE: these tests prove the rules and the sockets, not the network.
"""

import json
import os
import time
from typing import Any

import pytest

from districom_ops import firewall, services, winservice
from districom_ops.firewall import Rule
from districom_ops.winservice import State
from firewall_tests.conftest import metrics, rules_table
from services_tests.test_c2_services import CTX, cli, powershell

STORE_SYS_GROUP = "Store Sys (prueba C4)"
pytestmark = pytest.mark.timeout(1800)


def out(result: Any) -> Any:
    """The JSON a command prints last (its structured logs come before it)."""
    return json.loads(result.stdout.strip().splitlines()[-1])


def ps_json(command: str) -> Any:
    text = powershell(f"ConvertTo-Json -Depth 5 -Compress -InputObject @({command})").strip()
    return json.loads(text) if text else []


def rule_as_windows_says(name: str) -> dict[str, Any]:
    """One rule, read with its own filters (independent of the bulk read of ``firewall.py``)."""
    command = (
        f"$r = Get-NetFirewallRule -Name '{name}'; $p = $r | Get-NetFirewallPortFilter; "
        "$a = $r | Get-NetFirewallAddressFilter; $g = $r | Get-NetFirewallApplicationFilter; "
        "[pscustomobject]@{ name = [string]$r.Name; group = [string]$r.Group; action = [string]$r.Action; "
        "direction = [string]$r.Direction; profile = [string]$r.Profile; enabled = [string]$r.Enabled; "
        "protocol = [string]$p.Protocol; local_ports = @($p.LocalPort | % { [string]$_ }); "
        "remote_addresses = @($a.RemoteAddress | % { [string]$_ }); program = [string]$g.Program }"
    )
    found = ps_json(command)
    assert len(found) == 1, f"{name}: {len(found)} rules"
    item: dict[str, Any] = found[0]
    return item


def foreign_rules() -> list[Rule]:
    """Every rule that is not DistriCom's, with its scope: what C4-4 compares."""
    return sorted((r for r in firewall.read_all() if not r.name.startswith(firewall.PREFIX) and r.group != "DistriCom"),
                  key=lambda r: (r.name, r.key()))  # fmt: skip


def own_names() -> list[str]:
    return sorted(r.name for r in firewall.read_own())


def plan() -> firewall.Plan:
    return services.firewall_plan(CTX.install())


@pytest.fixture(scope="module", autouse=True)
def runner_firewall_on() -> Any:
    """The product never changes profiles (``verify`` reports one that is off). The runner's are switched on
    here, so the rules have an effect while the tests run; nothing else of the runner changes."""
    before = ps_json("Get-NetFirewallProfile | % { [pscustomobject]@{ name = $_.Name; on = [string]$_.Enabled } }")
    metrics["perfiles del runner al empezar"] = ", ".join(f"{p['name']}={p['on']}" for p in before)
    powershell("Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True")
    powershell(
        f"New-NetFirewallRule -Name 'StoreSys-C4-SQL' -DisplayName 'Store Sys SQL (prueba)' -Group '{STORE_SYS_GROUP}'"
        " -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1433 -RemoteAddress LocalSubnet | Out-Null; "
        f"New-NetFirewallRule -Name 'StoreSys-C4-App' -DisplayName 'Store Sys app (prueba)' -Group '{STORE_SYS_GROUP}'"
        " -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000,3000"
        " -Program 'C:\\StoreSys\\app.exe' | Out-Null"
    )
    yield
    powershell(f"Remove-NetFirewallRule -Group '{STORE_SYS_GROUP}' -ErrorAction SilentlyContinue")


@pytest.fixture(scope="module")
def foreign_before() -> list[Rule]:
    assert own_names() == [], "the runner must start without DistriCom rules"
    found = foreign_rules()
    metrics["reglas ajenas comparadas"] = len(found)
    assert {"StoreSys-C4-SQL", "StoreSys-C4-App"} <= {r.name for r in found}
    return found


@pytest.mark.criterion("C4-1")
@pytest.mark.criterion("C4-4")
def test_apply_creates_exactly_the_rules_of_the_plan(foreign_before: list[Rule]) -> None:
    started = time.monotonic()
    result = cli("firewall", "apply")
    metrics["firewall apply (s)"] = round(time.monotonic() - started, 1)
    changes = out(result)["changes"]
    assert len(changes) == len(plan().rules), changes
    assert own_names() == sorted(r.name for r in plan().rules)
    for wanted in plan().rules:
        item = rule_as_windows_says(wanted.name)
        rules_table.append({k: (", ".join(v) if isinstance(v, list) else v) for k, v in item.items()})
        found = Rule(item["name"], "", item["action"], item["protocol"], tuple(item["local_ports"]),
                     tuple(firewall.normalize_address(a) for a in item["remote_addresses"]), item["program"],
                     item["direction"], item["profile"], item["enabled"] == "True", item["group"])  # fmt: skip
        assert found.key() == wanted.key(), (wanted.name, item)
    assert foreign_rules() == foreign_before, "apply changed a rule that is not DistriCom's"


@pytest.mark.criterion("C4-1")
def test_the_public_rules_admit_only_the_lan_and_tailscale() -> None:
    lan = firewall.normalize_address(str(CTX.install().ops.lan_subnet))
    for name in ("DistriCom-HTTP-In", "DistriCom-HTTPS-In", "DistriCom-RDP-In"):
        item = rule_as_windows_says(name)
        remote = sorted(firewall.normalize_address(a) for a in item["remote_addresses"])
        assert remote == sorted([lan, firewall.TAILSCALE]), (name, remote)
        assert item["action"] == "Allow"
    for name in ("DistriCom-Block-PostgreSQL", "DistriCom-Block-Backend", "DistriCom-Block-Frontend"):
        item = rule_as_windows_says(name)
        assert item["action"] == "Block"
        assert item["program"] not in ("Any", ""), name


@pytest.mark.criterion("C4-2")
@pytest.mark.criterion("C4-4")
def test_applying_twice_changes_nothing(foreign_before: list[Rule]) -> None:
    ids = ps_json("Get-NetFirewallRule -Group 'DistriCom' | % { [string]$_.InstanceID }")
    result = cli("firewall", "apply")
    assert out(result)["changes"] == []
    assert ps_json("Get-NetFirewallRule -Group 'DistriCom' | % { [string]$_.InstanceID }") == ids, "rewritten"
    assert own_names() == sorted(r.name for r in plan().rules), "duplicated"
    assert foreign_rules() == foreign_before


@pytest.mark.criterion("C4-2")
def test_a_widened_rule_and_a_stray_rule_are_reported_and_corrected(foreign_before: list[Rule]) -> None:
    powershell("Set-NetFirewallRule -Name 'DistriCom-HTTPS-In' -RemoteAddress Any")
    powershell(
        "New-NetFirewallRule -Name 'DistriCom-Stray' -DisplayName 'stray' -Group 'DistriCom' -Direction Inbound"
        " -Action Allow -Protocol TCP -LocalPort 5432 | Out-Null"
    )
    verify = cli("firewall", "verify", check=False)
    assert verify.returncode == 1
    problems = out(verify)["problems"]
    assert any(p.startswith("DistriCom-HTTPS-In: not_as_plan") for p in problems), problems
    assert "DistriCom-Stray: unexpected" in problems, problems
    changes = out(cli("firewall", "apply"))["changes"]
    assert set(changes) == {"DistriCom-Stray: removed", "DistriCom-HTTPS-In: updated"}
    assert cli("firewall", "verify", check=False).returncode == 0
    assert foreign_rules() == foreign_before


@pytest.mark.criterion("C4-3")
@pytest.mark.criterion("C4-S")
def test_internal_ports_listen_only_on_loopback_with_the_rules_applied() -> None:
    install = CTX.install()
    cli("services", "start")
    status, body = services.https_get(install, "/api/v1/health/ready")
    assert status == 200, body[:200]
    internal = {i.port: i for i in plan().internal}
    command = (
        f"$ports = @({','.join(str(p) for p in internal)}); "
        "Get-NetTCPConnection -State Listen | ? { $ports -contains [int]$_.LocalPort } | % { "
        "[pscustomobject]@{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; "
        'program = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)").ExecutablePath } }'
    )
    sockets = ps_json(command)
    metrics["sockets internos"] = ", ".join(sorted(f"{s['address']}:{s['port']}" for s in sockets))
    assert {s["port"] for s in sockets} == set(internal), sockets
    for socket_ in sockets:
        assert socket_["address"] == "127.0.0.1", socket_
        rule_program = os.path.normcase(str(internal[socket_["port"]].program))
        assert os.path.normcase(socket_["program"]) == rule_program, (socket_, rule_program)
    verify = cli("firewall", "verify", "--services-running", check=False)
    report = out(verify)
    metrics["avisos de verify"] = "; ".join(report["warnings"]) or "ninguno"
    assert verify.returncode == 0, report["problems"]


@pytest.mark.criterion("C4-S")
def test_no_rule_of_districom_blocks_remote_desktop() -> None:
    for rule in firewall.read_own():
        if rule.action == "Block":
            assert not any(firewall._port_matches(p, firewall.RDP_PORT) for p in rule.local_ports), rule.name
            assert rule.program != "Any", rule.name
    assert rule_as_windows_says("DistriCom-RDP-In")["local_ports"] == ["3389"]
    cli("services", "stop")
    for name in services.ALL:
        assert winservice.status(name).state is State.STOPPED, name


@pytest.mark.criterion("C4-3")
def test_with_the_services_stopped_the_running_check_fails_and_the_static_one_passes() -> None:
    """The dynamic check requires the listeners: stopped services are listener_missing, never a pass."""
    running = cli("firewall", "verify", "--services-running", check=False)
    assert running.returncode == 1
    problems = out(running)["problems"]
    assert sorted(problems) == sorted(f"port {i.port}: listener_missing (the service is not listening)"
                                      for i in plan().internal), problems  # fmt: skip
    assert cli("firewall", "verify", check=False).returncode == 0


@pytest.mark.criterion("C4-R")
@pytest.mark.criterion("C4-4")
def test_remove_leaves_the_firewall_as_it_was_and_apply_restores_it(foreign_before: list[Rule]) -> None:
    before = sorted((r.name, r.key()) for r in firewall.read_own())
    removed = out(cli("firewall", "remove"))["removed"]
    assert sorted(removed) == sorted(r.name for r in plan().rules)
    assert own_names() == []
    assert foreign_rules() == foreign_before, "remove changed a rule that is not DistriCom's"
    assert out(cli("firewall", "remove"))["removed"] == []
    cli("firewall", "apply")
    assert sorted((r.name, r.key()) for r in firewall.read_own()) == before
    assert foreign_rules() == foreign_before
